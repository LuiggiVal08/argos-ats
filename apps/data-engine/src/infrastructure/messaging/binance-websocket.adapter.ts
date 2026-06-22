import WebSocket from "ws"
import { Tick } from "../../domain/entities/tick"
import {
  ExchangeConnectionState,
  ExchangeGateway,
  ExchangeInfo,
} from "../../application/ports/exchange-gateway.port"
import {
  BinanceTradeEvent,
  tickFromBinanceTrade,
} from "./in-memory-tick-buffer"
import { resolveWsBaseUrl } from "./exchange-ws-url.factory"

export interface BinanceWebSocketAdapterOptions {
  url?: string
  pingIntervalMs?: number
  pongTimeoutMs?: number
  reconnectBaseMs?: number
  reconnectMaxMs?: number
  maxReconnectAttempts?: number | null
  logger?: (msg: string) => void
  maxQueueSize?: number
}

const MAX_BACKOFF = 30_000
const BASE_BACKOFF = 1_000
const PONG_TIMEOUT_MS = 30_000

export class BinanceWebSocketAdapter implements ExchangeGateway {
  private ws: WebSocket | null = null
  private connState: ExchangeConnectionState = "idle"
  private pingTimer: NodeJS.Timeout | null = null
  private pongWatchdog: NodeJS.Timeout | null = null
  private reconnectTimer: NodeJS.Timeout | null = null
  private reconnectAttempt = 0
  private totalReconnects = 0
  private connectedAt: number | null = null
  private intentionalClose = false
  private onTickHandler: ((tick: Tick) => Promise<void>) | null = null
  private lastTickTs: number | null = null
  private readonly log: (msg: string) => void
  private readonly opts: Required<Omit<BinanceWebSocketAdapterOptions, "logger" | "maxQueueSize">>

  // Backpressure state
  private pendingCount = 0
  private wsPaused = false
  private droppedTicks = 0
  private readonly maxQueueSize: number

  private static defaultUrl(): string {
    return `${resolveWsBaseUrl()}/stream?streams=btcusdt@trade`
  }

  constructor(opts: BinanceWebSocketAdapterOptions = {}) {
    this.opts = {
      url: opts.url ?? BinanceWebSocketAdapter.defaultUrl(),
      pingIntervalMs: opts.pingIntervalMs ?? 30000,
      pongTimeoutMs: opts.pongTimeoutMs ?? PONG_TIMEOUT_MS,
      reconnectBaseMs: opts.reconnectBaseMs ?? BASE_BACKOFF,
      reconnectMaxMs: opts.reconnectMaxMs ?? MAX_BACKOFF,
      maxReconnectAttempts: opts.maxReconnectAttempts ?? null,
    }
    this.maxQueueSize = opts.maxQueueSize ?? 1000
    this.log =
      opts.logger ??
      // eslint-disable-next-line no-console
      ((m) => console.log(`[binance-ws] ${m}`))
  }

  get lastTickAgeMs(): number | null {
    if (this.lastTickTs === null) return null
    return Date.now() - this.lastTickTs
  }

  exchangeInfo(): ExchangeInfo {
    return {
      reconnectAttempt: this.reconnectAttempt,
      totalReconnects: this.totalReconnects,
      connectedAt: this.connectedAt,
      pendingTicks: this.pendingCount,
      droppedTicks: this.droppedTicks,
    }
  }

  private async processTick(tick: Tick): Promise<void> {
    if (this.pendingCount >= this.maxQueueSize) {
      this.droppedTicks++
      this.log(
        `overflow: dropping tick ${tick.tradeId} ` +
        `pending=${this.pendingCount} dropped=${this.droppedTicks}`,
      )
      return
    }
    this.pendingCount++
    this.maybePause()
    try {
      await this.onTickHandler!(tick)
    } finally {
      this.pendingCount--
      this.maybeResume()
    }
  }

  private maybePause(): void {
    if (this.pendingCount >= this.maxQueueSize && this.ws && !this.wsPaused) {
      this.wsPaused = true
      this.ws.pause()
      this.log(`ws paused (pending=${this.pendingCount})`)
    }
  }

  private maybeResume(): void {
    if (this.wsPaused && this.pendingCount < this.maxQueueSize * 0.8 && this.ws) {
      this.wsPaused = false
      this.ws.resume()
      this.log(`ws resumed (pending=${this.pendingCount})`)
    }
  }

  async start(onTick: (tick: Tick) => Promise<void>): Promise<void> {
    if (this.connState === "open" || this.connState === "connecting") {
      this.log(`start() ignored — already ${this.connState}`)
      return
    }
    this.intentionalClose = false
    this.reconnectAttempt = 0
    this.onTickHandler = onTick
    this.pendingCount = 0
    this.wsPaused = false
    this.droppedTicks = 0
    this.connState = "connecting"
    await this.open()
  }

  private async open(): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(this.opts.url)
      this.ws = ws

      ws.once("open", () => {
        this.connState = "open"
        this.connectedAt = Date.now()
        this.reconnectAttempt = 0
        this.log("connection open")
        this.pingTimer = setInterval(() => {
          ws.ping()
          this.pongWatchdog = setTimeout(() => {
            this.log("pong timeout — connection dead, forcing reconnect")
            ws.terminate()
            this.cleanupSocket()
            if (!this.intentionalClose) this.scheduleReconnect()
          }, this.opts.pongTimeoutMs)
        }, this.opts.pingIntervalMs)
        resolve()
      })

      ws.once("error", (err) => {
        this.log(`error: ${err.message}`)
        reject(err)
      })

      ws.on("pong", () => {
        if (this.pongWatchdog) {
          clearTimeout(this.pongWatchdog)
          this.pongWatchdog = null
        }
      })

      ws.on("message", (data) => {
        try {
          const msg = JSON.parse(data.toString("utf8")) as {
            stream?: string
            data?: BinanceTradeEvent
          }
          const trade = msg.data
          if (!trade || trade.e !== "trade") return
          this.lastTickTs = Date.now()
          const tick = tickFromBinanceTrade(trade)
          void this.processTick(tick)
        } catch (e) {
          this.log(`parse error: ${(e as Error).message}`)
        }
      })

      ws.on("close", (code, reason) => {
        this.log(`closed code=${code} reason=${reason.toString()}`)
        this.cleanupSocket()
        if (!this.intentionalClose) {
          this.totalReconnects++
          this.scheduleReconnect()
        }
      })
    })
  }

  private cleanupSocket(): void {
    this.connState = "closed"
    this.ws = null
    if (this.pongWatchdog) {
      clearTimeout(this.pongWatchdog)
      this.pongWatchdog = null
    }
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return

    const maxAttempts = this.opts.maxReconnectAttempts
    if (maxAttempts !== null && this.reconnectAttempt >= maxAttempts) {
      this.log(`max reconnect attempts (${maxAttempts}) reached — giving up`)
      return
    }

    const delay = Math.min(
      this.opts.reconnectBaseMs * Math.pow(2, this.reconnectAttempt),
      this.opts.reconnectMaxMs,
    )
    this.reconnectAttempt++
    this.log(`scheduling reconnect #${this.reconnectAttempt} in ${delay}ms`)

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      if (this.intentionalClose) return
      this.log(`reconnecting...`)
      this.connState = "connecting"
      if (this.onTickHandler) {
        void this.open().catch((err) => {
          this.log(`reconnect failed: ${err.message}`)
          this.scheduleReconnect()
        })
      }
    }, delay)
  }

  async close(): Promise<void> {
    if (this.connState === "closed" || this.connState === "idle") return
    this.intentionalClose = true
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.log("close() — sending close frame 1000")
    const ws = this.ws
    if (this.pongWatchdog) {
      clearTimeout(this.pongWatchdog)
      this.pongWatchdog = null
    }
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
    if (ws) {
      try { ws.close(1000, "orderly-shutdown") } catch { ws.terminate() }
    }
    this.connState = "closed"
    this.ws = null
  }

  state(): ExchangeConnectionState {
    return this.connState
  }
}
