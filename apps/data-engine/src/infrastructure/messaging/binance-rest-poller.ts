import { OpenInterest } from "../../domain/value-objects/open-interest"

const BINANCE_FUTURES_API = "https://fapi.binance.com"

export interface BinanceRestPollerOptions {
  symbols: string[]
  pollIntervalMs?: number
  logger?: (msg: string) => void
  onOpenInterest?: (oi: OpenInterest) => Promise<void>
}

export class BinanceRestPoller {
  private timer: NodeJS.Timeout | null = null
  private running = false
  private readonly symbols: string[]
  private readonly intervalMs: number
  private readonly log: (msg: string) => void
  private readonly onOi: ((oi: OpenInterest) => Promise<void>) | null

  constructor(opts: BinanceRestPollerOptions) {
    this.symbols = opts.symbols
    this.intervalMs = opts.pollIntervalMs ?? 60_000
    this.log = opts.logger ?? ((m) => {
      // eslint-disable-next-line no-console
      console.log(`[rest-poller] ${m}`)
    })
    this.onOi = opts.onOpenInterest ?? null
  }

  start(): void {
    if (this.running) return
    this.running = true
    this.log(`starting OI poller for ${this.symbols.join(", ")} every ${this.intervalMs}ms`)
    void this.poll()
    this.timer = setInterval(() => void this.poll(), this.intervalMs)
  }

  stop(): void {
    this.running = false
    if (this.timer) {
      clearInterval(this.timer)
      this.timer = null
    }
  }

  private async poll(): Promise<void> {
    for (const symbol of this.symbols) {
      try {
        const url = `${BINANCE_FUTURES_API}/fapi/v1/openInterest?symbol=${symbol.replace("/", "")}`
        const res = await fetch(url)
        if (!res.ok) {
          this.log(`OI fetch failed: ${res.status} for ${symbol}`)
          continue
        }
        const json = (await res.json()) as { symbol: string; openInterest: string }
        const oi = OpenInterest.create({
          symbol: json.symbol,
          openInterest: parseFloat(json.openInterest),
          ts: Date.now(),
        })
        if (this.onOi) void this.onOi(oi)
      } catch (err) {
        this.log(`OI poll error for ${symbol}: ${(err as Error).message}`)
      }
    }
  }
}
