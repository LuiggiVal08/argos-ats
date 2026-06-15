import { Injectable, Inject, OnModuleDestroy, OnModuleInit, Logger } from "@nestjs/common"
import Redis from "ioredis"
import { SYMBOLS, EVENT_STORE } from "../config/tokens"
import { EventStore } from "../../application/ports/event-store.port"
import { Symbol as SymbolVo } from "../../domain/value-objects/symbol"
import { TickData, CandleData } from "../../domain/entities/historical-event"
import { FeatureVectorData } from "../../domain/entities/feature-vector"
import { Timeframe } from "../../domain/value-objects/timeframe"

const log = (m: string): void => {
  Logger.log(m, "HistoricalPipelineService")
}

interface StreamSub {
  stream: string
  kind: "tick" | "candle" | "feature"
  parse: (raw: Record<string, unknown>) => {
    tick?: TickData
    candle?: CandleData
    feature?: FeatureVectorData
  }
}

function buildSubs(symbols: SymbolVo[]): StreamSub[] {
  const subs: StreamSub[] = []
  for (const sym of symbols) {
    const sid = sym.toStreamId().toLowerCase()
    subs.push({
      stream: `ticks:${sid}`,
      kind: "tick",
      parse: (r) => ({ tick: r as unknown as TickData }),
    })
    for (const tf of Timeframe.ALL) {
      subs.push({
        stream: `candles:${sid}:${tf}`,
        kind: "candle",
        parse: (r) => ({ candle: { ...r, isComplete: true } as unknown as CandleData }),
      })
    }
    for (const tf of Timeframe.ALL) {
      subs.push({
        stream: `features:${sid}:${tf}`,
        kind: "feature",
        parse: (r) => ({ feature: r as unknown as FeatureVectorData }),
      })
    }
  }
  return subs
}

@Injectable()
export class HistoricalPipelineService implements OnModuleInit, OnModuleDestroy {
  private client: Redis | null = null
  private timer: ReturnType<typeof setInterval> | null = null
  private readonly subs: StreamSub[]

  constructor(
    @Inject(EVENT_STORE) private readonly store: EventStore,
    @Inject(SYMBOLS) _symbols: SymbolVo[],
  ) {
    this.subs = buildSubs(_symbols)
  }

  async onModuleInit(): Promise<void> {
    const url = process.env.ARGOS_BROKER_URL
    if (!url) {
      log("ARGOS_BROKER_URL not set — historical pipeline disabled")
      return
    }
    this.client = new Redis(url, {
      connectTimeout: 2000,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    })
    this.poll()
    log(`started — polling ${this.subs.length} historical streams`)
  }

  async onModuleDestroy(): Promise<void> {
    if (this.timer) {
      clearInterval(this.timer)
      this.timer = null
    }
    if (this.client) {
      try { await this.client.quit() } catch { this.client.disconnect() }
      this.client = null
    }
    log("shutdown")
  }

  private poll(): void {
    const lastIds: Record<string, string> = {}
    for (const sub of this.subs) lastIds[sub.stream] = "$"

    this.timer = setInterval(async () => {
      if (!this.client) return
      for (const sub of this.subs) {
        try {
          const res = (await (
            this.client as unknown as {
              xread: (...args: Array<string | number>) => Promise<unknown>
            }
          ).xread(
            "BLOCK", 50, "COUNT", 20, "STREAMS", sub.stream, lastIds[sub.stream],
          )) as Array<[string, Array<[string, string[]]>]> | null
          if (!res) continue
          for (const [, entries] of res) {
            for (const [id, fields] of entries) {
              lastIds[sub.stream] = id
              const idx = fields.indexOf("p")
              if (idx === -1) continue
              const raw = fields[idx + 1]
              if (!raw) continue
              try {
                const parsed = JSON.parse(raw)
                const { tick, candle, feature } = sub.parse(parsed)
                if (tick) {
                  await this.store.store({ kind: "tick", data: tick })
                } else if (candle) {
                  await this.store.store({ kind: "candle", data: candle })
                } else if (feature) {
                  await this.store.store({ kind: "feature", data: feature })
                }
              } catch (e) {
                log(`error storing event from ${sub.stream}: ${(e as Error).message}`)
              }
            }
          }
        } catch { /* stream may not exist yet — skip */ }
      }
    }, 2_000)
  }
}
