import { Tick } from "../../domain/entities/tick"
import { Price } from "../../domain/value-objects/price"
import { Symbol } from "../../domain/value-objects/symbol"
import { TickBuffer } from "../../application/ports/tick-buffer.port"

export interface BinanceTradeEvent {
  e: "trade"
  E: number // event time
  s: string // symbol, e.g. "BTCUSDT"
  t: number // trade id
  p: string // price
  q: string // quantity
  T: number // trade time
  m: boolean // is buyer maker? true => seller is the aggressor => 'sell'
}

/**
 * Bounded FIFO in-memory buffer.
 *
 * Spec §5 Historia 1: max 100. When full, oldest tick is dropped
 * (FIFO eviction). drain() returns all ticks in insertion order and
 * resets the buffer to empty.
 *
 * Process-local. Not shared across instances of the data-engine.
 */
export class InMemoryTickBuffer implements TickBuffer {
  private readonly items: Tick[] = []

  constructor(public readonly max: number = 100) {
    if (max <= 0) {
      throw new Error("InMemoryTickBuffer: max must be > 0")
    }
  }

  async push(tick: Tick): Promise<void> {
    this.items.push(tick)
    while (this.items.length > this.max) {
      this.items.shift()
    }
  }

  size(): number {
    return this.items.length
  }

  capacity(): number {
    return this.max
  }

  async drain(): Promise<Tick[]> {
    const out = this.items.splice(0, this.items.length)
    return out
  }
}

/**
 * Parse a decimal quantity string as BigInt with 8 decimal places.
 *
 * Avoids Number() loss of precision for very small or very large
 * quantities (spec §5 Historia 1). Direct string-to-BigInt.
 *
 * Examples:
 *   "0.00000001"   → 1n
 *   "1.5"          → 150_000_000n
 *   "0.000000001"  → 0n  (< 1 satoshi → 0, will be rejected by Tick.create)
 */
export function parseQuantity(q: string): bigint {
  const dot = q.indexOf(".")
  const intPart = dot === -1 ? q : q.slice(0, dot)
  const decPart = dot === -1 ? "" : q.slice(dot + 1)
  const padded = decPart.slice(0, 8).padEnd(8, "0")
  const raw = intPart + padded
  const clean = raw.replace(/^0+/, "") || "0"
  return BigInt(clean)
}

/**
 * Helper to construct a Tick from a raw Binance trade event.
 * Keeps the WebSocket parsing logic out of the domain layer.
 */
export function tickFromBinanceTrade(evt: BinanceTradeEvent): Tick {
  // Binance streams are e.g. "btcusdt@trade". The `s` field is the
  // upper-case concatenated form ("BTCUSDT") which we split at the
  // quote boundary heuristically. For a single-symbol subscriber
  // this is fine; for multi-symbol we pass the expected base/quote.
  const symbol = symbolFromStream(evt.s)
  return Tick.create({
    symbol,
    price: Price.parse(evt.p, 8),
    quantity: parseQuantity(evt.q),
    side: evt.m ? "sell" : "buy",
    ts: evt.T,
    tradeId: String(evt.t),
  })
}

const KNOWN_QUOTES = ["USDT", "USDC", "BUSD", "FDUSD", "BTC", "ETH", "USD"]

export function symbolFromStream(raw: string): Symbol {
  const upper = raw.toUpperCase()
  for (const q of KNOWN_QUOTES) {
    if (upper.endsWith(q) && upper.length > q.length) {
      const base = upper.slice(0, upper.length - q.length)
      return Symbol.parse(`${base}/${q}`)
    }
  }
  // Fall back: treat as "BASE/UNKNOWN"; domain parser will reject if
  // format is wrong. Better to surface a clear error than to silently
  // mangle the symbol.
  return Symbol.parse(`${upper}/UNKNOWN`)
}
