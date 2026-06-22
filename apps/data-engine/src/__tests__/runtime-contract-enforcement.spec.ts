/**
 * Runtime Contract Enforcement tests.
 *
 * Validates that invalid payloads are rejected BEFORE side effects:
 *  - invalid candle → NOT published, system:errors emitted
 *  - invalid order → NOT executed, system:errors emitted
 *  - invalid fill   → NOT published, system:errors emitted
 *  - Execution use case hard gate prevents execution on invalid orders
 *
 * These tests use the contract validators directly to verify gate logic
 * without requiring a Redis instance.
 */

import { randomUUID } from "crypto"
import {
  validateCandle,
  validateOrder,
  validateFill,
  validateHeartbeat,
  validateError,
  ValidationResult,
} from "../contracts"

// ── Helpers ────────────────────────────────────────────────────────

function expectErrors(r: ValidationResult, field?: string): void {
  expect(r.ok).toBe(false)
  if (r.ok) throw new Error("expected errors but got ok")
  expect(r.errors.length).toBeGreaterThan(0)
  if (field) {
    expect(r.errors.some((e) => e.field === field)).toBe(true)
  }
}

function buildOrder(): Record<string, unknown> {
  const sigId = randomUUID()
  return {
    order_id: randomUUID(),
    signal_id: sigId,
    symbol: "BTC/USDT",
    side: "BUY",
    order_type: "MARKET",
    amount: "0.01",
    idempotency_key: `exec:${sigId}`,
    timestamp: Date.now(),
    schema_version: 1,
  }
}

// ── Phase 1A: Candle publisher gate ────────────────────────────────

describe("Candle Publisher Guard", () => {
  it("rejects invalid candle (wrong types)", () => {
    const result = validateCandle({
      symbol: 123,
      timeframe: "1h",
      open: "45000",
      high: "45200",
      low: "44900",
      close: "45100",
      volume: "1234",
      timestamp: Date.now(),
      schema_version: 1,
    })
    expectErrors(result, "symbol")
  })

  it("rejects candle with missing required fields", () => {
    expectErrors(validateCandle({}))
  })

  it("rejects candle with bad timeframe enum", () => {
    const p: Record<string, unknown> = {
      symbol: "BTC/USDT",
      timeframe: "2h",
      open: "45000",
      high: "45200",
      low: "44900",
      close: "45100",
      volume: "1234",
      timestamp: Date.now(),
      schema_version: 1,
    }
    expectErrors(validateCandle(p), "timeframe")
  })

  it("rejects candle with wrong schema_version", () => {
    const p: Record<string, unknown> = {
      symbol: "BTC/USDT",
      timeframe: "1h",
      open: "45000",
      high: "45200",
      low: "44900",
      close: "45100",
      volume: "1234",
      timestamp: Date.now(),
      schema_version: 2,
    }
    expectErrors(validateCandle(p), "schema_version")
  })
})

// ── Phase 1B: Order consumer gate ─────────────────────────────────

describe("Order Consumer Guard", () => {
  it("rejects invalid order (bad side)", () => {
    const p = buildOrder()
    p.side = "HOLD"
    expectErrors(validateOrder(p), "side")
  })

  it("rejects order with wrong idempotency_key prefix", () => {
    const p = buildOrder()
    p.idempotency_key = "wrong:prefix"
    expectErrors(validateOrder(p), "idempotency_key")
  })

  it("rejects non-MARKET order_type", () => {
    const p = buildOrder()
    p.order_type = "LIMIT"
    expectErrors(validateOrder(p), "order_type")
  })

  it("rejects order with missing amount", () => {
    const p = buildOrder()
    delete p.amount
    expectErrors(validateOrder(p), "amount")
  })

  it("rejects order with bad symbol pattern", () => {
    const p = buildOrder()
    p.symbol = "btc/usdt"
    expectErrors(validateOrder(p), "symbol")
  })
})

// ── Phase 1C: Fill publisher gate ─────────────────────────────────

describe("Fill Publisher Guard", () => {
  function buildFill(): Record<string, unknown> {
    return {
      fill_id: randomUUID(),
      order_id: randomUUID(),
      symbol: "BTC/USDT",
      side: "BUY",
      filled_qty: "0.01",
      avg_price: "45100.00",
      status: "FILLED",
      exchange_order_id: "binance-order-12345",
      timestamp: Date.now(),
      schema_version: 1,
    }
  }

  it("rejects invalid fill (bad status)", () => {
    const p = buildFill()
    p.status = "PENDING"
    expectErrors(validateFill(p), "status")
  })

  it("rejects fill with missing filled_qty", () => {
    const p = buildFill()
    delete p.filled_qty
    expectErrors(validateFill(p), "filled_qty")
  })

  it("rejects fill with bad side enum", () => {
    const p = buildFill()
    p.side = "HOLD"
    expectErrors(validateFill(p), "side")
  })

  it("rejects fill with non-numeric avg_price", () => {
    const p = buildFill()
    p.avg_price = "not-a-number"
    expectErrors(validateFill(p), "avg_price")
  })
})

// ── Phase 1D: System streams enforcement ──────────────────────────

describe("Heartbeat Publisher Guard", () => {
  function buildHeartbeat(): Record<string, unknown> {
    return {
      service: "data-engine",
      status: "healthy",
      uptime_seconds: 3600,
      mode: "PAPER_TRADING",
      broker_ok: true,
      exchange_ok: true,
      loops_alive: 4,
      timestamp: Date.now(),
      schema_version: 1,
    }
  }

  it("rejects heartbeat with bad service enum", () => {
    const p = buildHeartbeat()
    p.service = "not-a-service"
    expectErrors(validateHeartbeat(p), "service")
  })

  it("rejects heartbeat with bad mode enum", () => {
    const p = buildHeartbeat()
    p.mode = "INVALID_MODE"
    expectErrors(validateHeartbeat(p), "mode")
  })

  it("rejects heartbeat with missing status", () => {
    const p = buildHeartbeat()
    delete p.status
    expectErrors(validateHeartbeat(p), "status")
  })

  it("rejects heartbeat with wrong broker_ok type", () => {
    const p = buildHeartbeat()
    p.broker_ok = "yes"
    expectErrors(validateHeartbeat(p), "broker_ok")
  })
})

describe("Error Publisher Guard", () => {
  function buildError(): Record<string, unknown> {
    return {
      error_id: randomUUID(),
      service: "data-engine",
      severity: "ERROR",
      error_code: "SOME_ERROR",
      message: "Something went wrong",
      metadata: { foo: "bar" },
      timestamp: Date.now(),
      schema_version: 1,
    }
  }

  it("rejects error with missing error_id", () => {
    const p = buildError()
    delete p.error_id
    expectErrors(validateError(p), "error_id")
  })

  it("rejects error with bad severity enum", () => {
    const p = buildError()
    p.severity = "INFO"
    expectErrors(validateError(p), "severity")
  })

  it("rejects error with bad error_id pattern", () => {
    const p = buildError()
    p.error_id = "not-a-uuid"
    expectErrors(validateError(p), "error_id")
  })

  it("rejects error with missing message", () => {
    const p = buildError()
    delete p.message
    expectErrors(validateError(p), "message")
  })
})

// ── Phase 1E: Execution use case hard gate (contract validation) ──

describe("Execution Hard Gate - Contract Validation", () => {
  it("rejects order with missing signal_id", () => {
    const p = buildOrder()
    delete p.signal_id
    expectErrors(validateOrder(p), "signal_id")
  })

  it("rejects order with wrong amount format", () => {
    const p = buildOrder()
    p.amount = "not-a-decimal"
    expectErrors(validateOrder(p), "amount")
  })

  it("rejects order with missing order_id", () => {
    const p = buildOrder()
    delete p.order_id
    expectErrors(validateOrder(p), "order_id")
  })

  it("rejects order with negative timestamp", () => {
    const p = buildOrder()
    p.timestamp = -1
    expectErrors(validateOrder(p), "timestamp")
  })
})
