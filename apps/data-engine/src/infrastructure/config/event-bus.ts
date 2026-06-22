export enum EventStream {
  TICKS = "ticks",
  CANDLES = "candles",
  FEATURES = "features",
  SIGNALS = "signals",
  ORDERS = "orders",
  POSITIONS = "positions",
  NOTIFICATIONS = "notifications",
  METRICS = "metrics",
}

export enum ObservabilityStream {
  SYSTEM_METRICS = "system:metrics",
  SYSTEM_LATENCY = "system:latency",
  SYSTEM_CONTRACT_VIOLATIONS = "system:contract_violations",
  SYSTEM_ERRORS = "system:errors",
  SYSTEM_HEARTBEAT = "system:heartbeat",
}

export function streamName(prefix: EventStream, symbol: string, timeframe?: string): string {
  return timeframe
    ? `${prefix}:${symbol.toLowerCase()}:${timeframe}`
    : `${prefix}:${symbol.toLowerCase()}`
}
