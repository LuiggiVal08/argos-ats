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
import { FundingRate } from "../../domain/value-objects/funding-rate"
import { AggTrade } from "../../domain/value-objects/agg-trade"

export interface BinanceWebSocketAdapterOptions {
  url?: string
  pingIntervalMs?: number
  pongTimeoutMs?: number
  reconnectBaseMs?: number
  reconnectMaxMs?: number
  maxReconnectAttempts?: number | null
  logger?: (msg: string) => void
  additionalStreams?: boolean
}

const DEFAULT_URL =
  "wss://stream.binance.com:9443/stream?streams=btcusdt@trade"
const MAX_BACKOFF = 30_000
const BASE_BACKOFF = 1_000
const PONG_TIMEOUT_MS = 10_000

interface BinanceMarkPriceEvent {
  e: "markPriceUpdate"
  s: string
  p: string
  i: string
  P: string
  r: string
  T: number
  E: number
}

interface BinanceAggTradeEvent {
  e: "aggTrade"
  s: string
  a: number
  p: string
  q: string
  f: number
  l: number
  T: number
  m: boolean
  M: boolean
}

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
  private onFundingRateHandler: ((fr: FundingRate) => Promise<void>) | null = null
  private onAggTradeHandler: ((trade: AggTrade) => Promise<void>) | null = null
  private lastTickTs: number | null = null
  private readonly log: (msg: string) => void
  private readonly opts: Required<Omit<BinanceWebSocketAdapterOptions, "logger">>

  constructor(opts: BinanceWebSocketAdapterOptions = {}) {
    this.opts = {
      url: opts.url ?? DEFAULT_URL,
      pingIntervalMs: opts.pingIntervalMs ?? 30000,
      pongTimeoutMs: opts.pongTimeoutMs ?? PONG_TIMEOUT_MS,
      reconnectBaseMs: opts.reconnectBaseMs ?? BASE_BACKOFF,
      reconnectMaxMs: opts.reconnectMaxMs ?? MAX_BACKOFF,
      maxReconnectAttempts: opts.maxReconnectAttempts ?? null,
      additionalStreams: opts.additionalStreams ?? false,
    }
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
    }
  }

  async start(
    onTick: (tick: Tick) => Promise<void>,
    onFundingRate?: (fr: FundingRate) => Promise<void>,
    onAggTrade?: (trade: AggTrade) => Promise<void>,
  ): Promise<void> {
    if (this.connState === "open" || this.connState === "connecting") {
      this.log(`start() ignored — already ${this.connState}`)
      return
    }
    this.intentionalClose = false
    this.reconnectAttempt = 0
    this.onTickHandler = onTick
    this.onFundingRateHandler = onFundingRate ?? null
    this.onAggTradeHandler = onAggTrade ?? null
    this.connState = "connecting"
    await this.open(onTick, onFundingRate, onAggTrade)
  }

  private async open(
    onTick: (tick: Tick) => Promise<void>,
    onFundingRate?: (fr: FundingRate) => Promise<void>,
    onAggTrade?: (trade: AggTrade) => Promise<void>,
  ): Promise<void> {
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
            data?: Record<string, unknown>
          }
          if (!msg.data) return
          const d = msg.data as Record<string, unknown>

          if (d.e === "trade") {
            this.lastTickTs = Date.now()
            const tick = tickFromBinanceTrade(d as unknown as BinanceTradeEvent)
            void onTick(tick)
          } else if (d.e === "markPriceUpdate" && onFundingRate) {
            const m = d as unknown as BinanceMarkPriceEvent
            const fr = FundingRate.create({
              symbol: m.s,
              fundingRate: parseFloat(m.r),
              markPrice: parseFloat(m.p),
              indexPrice: parseFloat(m.i),
              settlePrice: parseFloat(m.P),
              nextFundingTime: m.T,
              ts: m.E,
            })
            void onFundingRate(fr)
          } else if (d.e === "aggTrade" && onAggTrade) {
            const a = d as unknown as BinanceAggTradeEvent
            const trade = AggTrade.create({
              symbol: a.s,
              tradeId: a.a,
              price: parseFloat(a.p),
              quantity: parseFloat(a.q),
              isBuyerMaker: a.m,
              ts: a.T,
            })
            void onAggTrade(trade)
          }
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
      const h = this.onTickHandler
      if (h) {
        void this.open(h, this.onFundingRateHandler ?? undefined, this.onAggTradeHandler ?? undefined).catch((err) => {
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
