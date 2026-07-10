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
const PONG_TIMEOUT_MS = 120_000

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
  private connectionId = 0
  private onTickHandler: ((tick: Tick) => Promise<void>) | null = null
  private lastTickTs: number | null = null
  private lastActivityAt: number | null = null
  private lastPongAt: number | null = null
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
      connectionId: this.connectionId,
      pendingTicks: this.pendingCount,
      droppedTicks: this.droppedTicks,
      lastActivityAt: this.lastActivityAt,
      lastPongAt: this.lastPongAt,
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
      const handler = this.onTickHandler
      if (typeof handler !== "function") {
        this.log("onTickHandler not registered — dropping tick")
        return
      }
      await handler(tick)
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
    const myId = ++this.connectionId
    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(this.opts.url)
      this.ws = ws

      ws.once("open", () => {
        this.connState = "open"
        this.connectedAt = Date.now()
        this.lastActivityAt = Date.now()
        this.lastPongAt = null
        this.reconnectAttempt = 0
        this.log(`connection open (id=${myId})`)
        if (this.pingTimer) {
          clearInterval(this.pingTimer)
          this.pingTimer = null
        }
        this.pingTimer = setInterval(() => {
          const age = this.lastActivityAt ? Date.now() - this.lastActivityAt : -1
          this.log(`ping sent connection_id=${myId} last_activity_age=${age}ms`)
          if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.ping()
          }
          if (this.pongWatchdog) {
            clearTimeout(this.pongWatchdog)
            this.pongWatchdog = null
          }
          const watchdogId = setTimeout(() => {
            if (myId !== this.connectionId) return
            const elapsed = this.lastActivityAt ? Date.now() - this.lastActivityAt : -1
            if (elapsed < this.opts.pongTimeoutMs) {
              this.log(`watchdog_skip: last_activity_age=${elapsed}ms < threshold=${this.opts.pongTimeoutMs}ms — connection healthy`)
              return
            }
            this.log(`watchdog_fire: last_activity_age=${elapsed}ms threshold=${this.opts.pongTimeoutMs}ms — connection dead, forcing reconnect`)
            if (this.ws && myId === this.connectionId) {
              this.ws.terminate()
            }
            this.cleanupSocket()
            if (!this.intentionalClose) this.scheduleReconnect()
          }, this.opts.pongTimeoutMs)
          this.pongWatchdog = watchdogId
        }, this.opts.pingIntervalMs)
        resolve()
      })

      ws.once("error", (err) => {
        this.log(`error: ${err.message}`)
        reject(err)
      })

      ws.on("pong", () => {
        this.lastPongAt = Date.now()
        this.lastActivityAt = this.lastPongAt
        this.log(`pong received (last_activity_age=0ms — just refreshed)`)
        if (this.pongWatchdog) {
          clearTimeout(this.pongWatchdog)
          this.pongWatchdog = null
        }
      })

      ws.on("message", (data) => {
        this.lastActivityAt = Date.now()
        try {
          const msg = JSON.parse(data.toString("utf8")) as {
            stream?: string
            data?: BinanceTradeEvent
          }
          const trade = msg.data
          if (!trade || trade.e !== "trade") return
          this.lastTickTs = Date.now()
          const tick = tickFromBinanceTrade(trade)
          this.processTick(tick).catch((err) => {
            this.log(
              `tick processing error: ${err.message} ` +
              `trade=${tick.tradeId} price=${tick.price} qty=${tick.quantity}`,
            )
          })
        } catch (e) {
          this.log(`parse error: ${(e as Error).message}`)
        }
      })

      ws.on("close", (code, reason) => {
        if (this.ws !== ws) {
          this.log(`ignoring stale close from connection #${myId} (current is #${this.connectionId})`)
          return
        }
        const reasonStr = reason.toString() || "(no reason)"
        const lastActivityAge = this.lastActivityAt ? Date.now() - this.lastActivityAt : 'never'
        this.log(`closed code=${code} reason="${reasonStr}" last_activity_age=${lastActivityAge}ms connection_id=${myId}`)
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
