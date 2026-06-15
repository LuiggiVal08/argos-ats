import { Tick } from "../../domain/entities/tick"

export type ExchangeConnectionState = "idle" | "connecting" | "open" | "closed"

/**
 * Exchange WebSocket gateway port.
 *
 * Exchange-agnostic. The adapter (e.g. BinanceWebSocketAdapter) handles
 * the protocol details of a specific exchange (Binance, Bybit, OKX, ...).
 *
 * The application depends only on this interface — never on `ws`,
 * ccxt's `watch*` methods, or any exchange-specific library.
 */
export interface ExchangeGateway {
  /**
   * Open a connection to the exchange and start emitting ticks via
   * the handler. The handler MUST be safe to call concurrently.
   * Idempotent: calling start() on an open connection is a no-op.
   */
  start(onTick: (tick: Tick) => Promise<void>): Promise<void>

  /**
   * Orderly close of the WebSocket connection. Flushes any pending
   * messages, sends the appropriate close frame, and releases the
   * socket. After close(), start() may be called again to reconnect.
   */
  close(): Promise<void>

  /** Current connection state. */
  state(): ExchangeConnectionState

  /**
   * Milliseconds since the last tick was received, or null if no
   * tick has been received yet (e.g. connecting state).
   */
  lastTickAgeMs: number | null
}
