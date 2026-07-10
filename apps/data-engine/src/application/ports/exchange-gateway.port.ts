import { Tick } from "../../domain/entities/tick"

export type ExchangeConnectionState = "idle" | "connecting" | "open" | "closed"

export interface ExchangeInfo {
  reconnectAttempt: number
  totalReconnects: number
  connectedAt: number | null
  connectionId: number
  pendingTicks?: number
  droppedTicks?: number
  lastActivityAt?: number | null
  lastPongAt?: number | null
}

export interface ExchangeGateway {
  start(onTick: (tick: Tick) => Promise<void>): Promise<void>

  close(): Promise<void>

  state(): ExchangeConnectionState

  lastTickAgeMs: number | null

  exchangeInfo(): ExchangeInfo
}
