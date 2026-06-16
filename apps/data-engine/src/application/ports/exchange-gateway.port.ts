import { Tick } from "../../domain/entities/tick"
import { FundingRate } from "../../domain/value-objects/funding-rate"
import { AggTrade } from "../../domain/value-objects/agg-trade"

export type ExchangeConnectionState = "idle" | "connecting" | "open" | "closed"

export interface ExchangeInfo {
  reconnectAttempt: number
  totalReconnects: number
  connectedAt: number | null
}

export interface ExchangeGateway {
  start(
    onTick: (tick: Tick) => Promise<void>,
    onFundingRate?: (fr: FundingRate) => Promise<void>,
    onAggTrade?: (trade: AggTrade) => Promise<void>,
  ): Promise<void>

  close(): Promise<void>

  state(): ExchangeConnectionState

  lastTickAgeMs: number | null

  exchangeInfo(): ExchangeInfo
}
