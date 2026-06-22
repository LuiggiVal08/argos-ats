import { CheckpointReader } from "../state/checkpoint-reader"

export interface RecoveryState {
  lastProcessedTimestamp: number
  lastCandleTimestamp: number
  lastSignalId: string | null
  lastOrderId: string | null
  lastFillId: string | null
  openPositions: string
  equity: number
  mode: string
  isResume: boolean
}

export interface RecoveredFill {
  fillId: string
  orderId: string
  symbol: string
  side: string
  filledQty: string
  avgPrice: string
  status: string
  timestamp: number
}

export interface TruthStoreReplay {
  fills: RecoveredFill[]
  lastOrderId: string | null
  lastFillId: string | null
  orderCount: number
  fillCount: number
}

export function freshStart(mode: string): RecoveryState {
  return {
    lastProcessedTimestamp: 0,
    lastCandleTimestamp: 0,
    lastSignalId: null,
    lastOrderId: null,
    lastFillId: null,
    openPositions: "[]",
    equity: 5000,
    mode,
    isResume: false,
  }
}

export interface CheckpointData {
  last_processed_timestamp: number
  last_candle_timestamp: number
  last_signal_id: string | null
  last_order_id: string | null
  last_fill_id: string | null
  open_positions: string
  equity: number
  mode: string
  engine: string
  schema_version: number
}

export class RecoveryEngine {
  private readonly checkpointReader: CheckpointReader
  private readonly mode: string

  constructor(
    checkpointPath: string,
    mode: string,
  ) {
    this.checkpointReader = new CheckpointReader(checkpointPath)
    this.mode = mode
  }

  restore(resumeMode: boolean, replay?: TruthStoreReplay): RecoveryState {
    if (!resumeMode) {
      return freshStart(this.mode)
    }

    const cp = this.checkpointReader.readWithFallback<CheckpointData | null>(null)

    if (!cp) {
      return freshStart(this.mode)
    }

    if (cp.schema_version !== 1) {
      return freshStart(this.mode)
    }

    let lastOrderId = cp.last_order_id ?? null
    let lastFillId = cp.last_fill_id ?? null

    if (replay) {
      if (replay.lastOrderId && replay.lastOrderId !== lastOrderId) {
        lastOrderId = replay.lastOrderId
      }
      if (replay.lastFillId && replay.lastFillId !== lastFillId) {
        lastFillId = replay.lastFillId
      }
    }

    return {
      lastProcessedTimestamp: cp.last_processed_timestamp,
      lastCandleTimestamp: cp.last_candle_timestamp,
      lastSignalId: cp.last_signal_id ?? null,
      lastOrderId,
      lastFillId,
      openPositions: cp.open_positions ?? "[]",
      equity: cp.equity ?? 5000,
      mode: cp.mode,
      isResume: true,
    }
  }

  shouldProcessOrder(orderId: string, state: RecoveryState): boolean {
    if (!state.isResume) return true
    if (!state.lastOrderId) return true
    return orderId > state.lastOrderId
  }

  shouldProcessFill(fillId: string, state: RecoveryState): boolean {
    if (!state.isResume) return true
    if (!state.lastFillId) return true
    return fillId > state.lastFillId
  }

  shouldProcessSignal(signalId: string, state: RecoveryState): boolean {
    if (!state.isResume) return true
    if (!state.lastSignalId) return true
    return signalId > state.lastSignalId
  }

  isCandleBeforeCheckpoint(candleTimestamp: number, state: RecoveryState): boolean {
    if (!state.isResume) return false
    if (state.lastCandleTimestamp === 0) return false
    return candleTimestamp <= state.lastCandleTimestamp
  }
}
