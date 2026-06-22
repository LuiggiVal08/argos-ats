/**
 * Recovery & Resume validation test.
 *
 * Simulates:
 *   1. Run forward test (simulated state progression)
 *   2. Simulate crash (checkpoint written, process killed)
 *   3. Resume with RESUME_MODE=true
 *   4. Validate: no duplicate trades, replay alignment, checkpoint consistency
 *
 * This test validates the RecoveryEngine logic without requiring a
 * live Redis or exchange connection.
 */

import { randomUUID } from "crypto"
import { unlinkSync, existsSync, mkdirSync } from "fs"
import { join, resolve } from "path"
import { RecoveryEngine, TruthStoreReplay } from "../../infrastructure/recovery/recovery-engine"
import { CheckpointWriter, Checkpoint } from "../../infrastructure/state/checkpoint-writer"
import { CheckpointReader } from "../../infrastructure/state/checkpoint-reader"

const TMP_STATE = resolve(process.cwd(), "tmp-test-state")

function tmpCheckpointPath(): string {
  return join(TMP_STATE, "checkpoint.de.json")
}

function cleanState(): void {
  try {
    if (existsSync(TMP_STATE)) {
      const p = tmpCheckpointPath()
      if (existsSync(p)) unlinkSync(p)
      // rm dir
    }
  } catch {
    // ok
  }
}

// ── Test helpers ──────────────────────────────────────────────────

function makeSignalId(seq: number): string {
  // deterministic "UUID" that sorts by seq
  const hex = seq.toString(16).padStart(32, "0")
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function makeOrderId(seq: number): string {
  return makeSignalId(seq)
}

function makeFillId(seq: number): string {
  return makeSignalId(seq)
}

// ── Phase A: Checkpoint IO ───────────────────────────────────────

describe("Checkpoint IO", () => {
  beforeEach(() => cleanState())
  afterAll(() => cleanState())

  it("writes and reads a checkpoint", async () => {
    const writer = new CheckpointWriter(tmpCheckpointPath(), 1000)
    const data: Checkpoint = {
      last_processed_timestamp: Date.now(),
      last_candle_timestamp: Date.now(),
      last_signal_id: randomUUID(),
      last_order_id: randomUUID(),
      last_fill_id: randomUUID(),
      open_positions: "[]",
      equity: 5000,
      mode: "LIVE_SIMULATION",
      engine: "data-engine",
      schema_version: 1,
    }
    await writer.write(data)

    const reader = new CheckpointReader(tmpCheckpointPath())
    expect(reader.exists()).toBe(true)
    const loaded = reader.readSync<Checkpoint>()
    expect(loaded.equity).toBe(5000)
    expect(loaded.mode).toBe("LIVE_SIMULATION")
    expect(loaded.engine).toBe("data-engine")
    expect(loaded.schema_version).toBe(1)
  })

  it("reader returns fallback when no checkpoint", () => {
    const reader = new CheckpointReader("/nonexistent/checkpoint.json")
    expect(reader.exists()).toBe(false)
    const fallback = { fallback: true }
    const result = reader.readWithFallback(fallback)
    expect(result).toEqual(fallback)
  })
})

// ── Phase B: RecoveryEngine fresh start ──────────────────────────

describe("RecoveryEngine — fresh start", () => {
  it("returns fresh state when resume=false", () => {
    const engine = new RecoveryEngine(tmpCheckpointPath(), "LIVE_SIMULATION")
    const state = engine.restore(false)
    expect(state.isResume).toBe(false)
    expect(state.lastOrderId).toBeNull()
    expect(state.lastSignalId).toBeNull()
    expect(state.equity).toBe(5000)
  })
})

// ── Phase C: RecoveryEngine resume ───────────────────────────────

describe("RecoveryEngine — resume from checkpoint", () => {
  const sigId = makeSignalId(42)
  const orderId = makeOrderId(7)
  const fillId = makeFillId(3)

  beforeEach(() => {
    cleanState()
    if (!existsSync(TMP_STATE)) mkdirSync(TMP_STATE, { recursive: true })
    // Write checkpoint manually
    const cp: Checkpoint = {
      last_processed_timestamp: 1000,
      last_candle_timestamp: 500,
      last_signal_id: sigId,
      last_order_id: orderId,
      last_fill_id: fillId,
      open_positions: JSON.stringify([{ symbol: "BTC/USDT", side: "BUY", qty: "0.01" }]),
      equity: 4920.50,
      mode: "LIVE_SIMULATION",
      engine: "data-engine",
      schema_version: 1,
    }
    const writer = new CheckpointWriter(tmpCheckpointPath(), 1000)
    writer.write(cp)
  })

  afterAll(() => cleanState())

  it("restores state from checkpoint with resume=true", () => {
    const engine = new RecoveryEngine(tmpCheckpointPath(), "LIVE_SIMULATION")
    const state = engine.restore(true)
    expect(state.isResume).toBe(true)
    expect(state.lastOrderId).toBe(orderId)
    expect(state.lastSignalId).toBe(sigId)
    expect(state.lastFillId).toBe(fillId)
    expect(state.lastCandleTimestamp).toBe(500)
    expect(state.lastProcessedTimestamp).toBe(1000)
    expect(state.equity).toBe(4920.50)
    expect(JSON.parse(state.openPositions)).toHaveLength(1)
  })

  it("rejects signals before or equal to lastSignalId", () => {
    const engine = new RecoveryEngine(tmpCheckpointPath(), "LIVE_SIMULATION")
    const state = engine.restore(true)

    expect(engine.shouldProcessSignal(makeSignalId(41), state)).toBe(false)
    expect(engine.shouldProcessSignal(makeSignalId(42), state)).toBe(false)
    expect(engine.shouldProcessSignal(makeSignalId(43), state)).toBe(true)
  })

  it("rejects orders before or equal to lastOrderId", () => {
    const engine = new RecoveryEngine(tmpCheckpointPath(), "LIVE_SIMULATION")
    const state = engine.restore(true)

    expect(engine.shouldProcessOrder(makeOrderId(6), state)).toBe(false)
    expect(engine.shouldProcessOrder(makeOrderId(7), state)).toBe(false)
    expect(engine.shouldProcessOrder(makeOrderId(8), state)).toBe(true)
  })

  it("rejects fills before or equal to lastFillId", () => {
    const engine = new RecoveryEngine(tmpCheckpointPath(), "LIVE_SIMULATION")
    const state = engine.restore(true)

    expect(engine.shouldProcessFill(makeFillId(2), state)).toBe(false)
    expect(engine.shouldProcessFill(makeFillId(3), state)).toBe(false)
    expect(engine.shouldProcessFill(makeFillId(4), state)).toBe(true)
  })

  it("ignores candles before checkpoint", () => {
    const engine = new RecoveryEngine(tmpCheckpointPath(), "LIVE_SIMULATION")
    const state = engine.restore(true)

    expect(engine.isCandleBeforeCheckpoint(400, state)).toBe(true)
    expect(engine.isCandleBeforeCheckpoint(500, state)).toBe(true)
    expect(engine.isCandleBeforeCheckpoint(501, state)).toBe(false)
  })
})

// ── Phase D: Replay alignment ────────────────────────────────────

describe("RecoveryEngine — replay alignment (Truth Store wins)", () => {
  beforeEach(() => {
    cleanState()
    if (!existsSync(TMP_STATE)) mkdirSync(TMP_STATE, { recursive: true })
    // Checkpoint has older IDs
    const cp: Checkpoint = {
      last_processed_timestamp: 1000,
      last_candle_timestamp: 500,
      last_signal_id: makeSignalId(10),
      last_order_id: makeOrderId(5),
      last_fill_id: makeFillId(2),
      open_positions: "[]",
      equity: 5000,
      mode: "LIVE_SIMULATION",
      engine: "data-engine",
      schema_version: 1,
    }
    const writer = new CheckpointWriter(tmpCheckpointPath(), 1000)
    writer.write(cp)
  })

  afterAll(() => cleanState())

  it("replay overrides checkpoint when replay has newer IDs", () => {
    const engine = new RecoveryEngine(tmpCheckpointPath(), "LIVE_SIMULATION")
    const replay: TruthStoreReplay = {
      fills: [],
      lastOrderId: makeOrderId(8),
      lastFillId: makeFillId(4),
      orderCount: 10,
      fillCount: 5,
    }

    const state = engine.restore(true, replay)
    // Replay's lastOrderId (8) > checkpoint's (5) → replay wins
    expect(state.lastOrderId).toBe(makeOrderId(8))
    // Replay's lastFillId (4) > checkpoint's (2) → replay wins
    expect(state.lastFillId).toBe(makeFillId(4))
    // No replay for signal → checkpoint wins
    expect(state.lastSignalId).toBe(makeSignalId(10))
  })

  it("processSignal bypass when fresh start", () => {
    const engine = new RecoveryEngine(tmpCheckpointPath(), "LIVE_SIMULATION")
    const state = engine.restore(false)
    // Fresh start — everything should be processed
    expect(engine.shouldProcessSignal(makeSignalId(1), state)).toBe(true)
    expect(engine.shouldProcessOrder(makeOrderId(1), state)).toBe(true)
    expect(engine.shouldProcessFill(makeFillId(1), state)).toBe(true)
    expect(engine.isCandleBeforeCheckpoint(0, state)).toBe(false)
  })
})

// ── Phase E: Full crash-sim scenario ─────────────────────────────

describe("Full crash-resume scenario", () => {
  beforeEach(() => cleanState())
  afterAll(() => cleanState())

  it("no duplicate trades after resume", () => {
    // ── Step 1: Simulate forward progress ──
    if (!existsSync(TMP_STATE)) mkdirSync(TMP_STATE, { recursive: true })
    const writer = new CheckpointWriter(tmpCheckpointPath(), 1000)

    // Process 5 signals, 3 orders, 2 fills
    const lastSignal = makeSignalId(5)
    const lastOrder = makeOrderId(3)
    const lastFill = makeFillId(2)

    const cp: Checkpoint = {
      last_processed_timestamp: 5000,
      last_candle_timestamp: 2500,
      last_signal_id: lastSignal,
      last_order_id: lastOrder,
      last_fill_id: lastFill,
      open_positions: JSON.stringify([{ symbol: "BTC/USDT", side: "BUY", qty: "0.01" }]),
      equity: 4980,
      mode: "LIVE_SIMULATION",
      engine: "data-engine",
      schema_version: 1,
    }
    writer.write(cp)

    // ── Step 2: Crash! Process killed ──
    // ── Step 3: Resume ──
    const engine = new RecoveryEngine(tmpCheckpointPath(), "LIVE_SIMULATION")
    // Simulate replay from truth store — knows about 2 fills
    const replay: TruthStoreReplay = {
      fills: [
        { fillId: makeFillId(1), orderId: makeOrderId(1), symbol: "BTC/USDT", side: "BUY", filledQty: "0.005", avgPrice: "45000", status: "FILLED", timestamp: 1000 },
        { fillId: makeFillId(2), orderId: makeOrderId(2), symbol: "BTC/USDT", side: "BUY", filledQty: "0.005", avgPrice: "45100", status: "FILLED", timestamp: 2000 },
      ],
      lastOrderId: lastOrder,
      lastFillId: lastFill,
      orderCount: 3,
      fillCount: 2,
    }
    const state = engine.restore(true, replay)

    // ── Step 4: Verify no duplication ──
    // Signal 5 already processed → reject
    expect(engine.shouldProcessSignal(makeSignalId(5), state)).toBe(false)
    // Signal 6 new → accept
    expect(engine.shouldProcessSignal(makeSignalId(6), state)).toBe(true)

    // Order 3 already processed → reject
    expect(engine.shouldProcessOrder(makeOrderId(3), state)).toBe(false)
    // Order 4 new → accept
    expect(engine.shouldProcessOrder(makeOrderId(4), state)).toBe(true)

    // Fill 2 already processed → reject
    expect(engine.shouldProcessFill(makeFillId(2), state)).toBe(false)
    // Fill 3 new → accept
    expect(engine.shouldProcessFill(makeFillId(3), state)).toBe(true)

    // Candles before checkpoint → ignore
    expect(engine.isCandleBeforeCheckpoint(2500, state)).toBe(true)
    expect(engine.isCandleBeforeCheckpoint(2501, state)).toBe(false)

    // Equity preserved
    expect(state.equity).toBe(4980)
    expect(state.isResume).toBe(true)
  })
})
