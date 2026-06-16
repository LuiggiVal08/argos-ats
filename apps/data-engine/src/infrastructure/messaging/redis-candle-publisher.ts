import Redis from "ioredis"
import { Candle } from "../../domain/entities/candle"
import { CandlePublisher } from "../../application/ports/candle-publisher.port"

export interface RedisCandlePublisherOptions {
  url: string
  streamPrefix: string
  connectTimeoutMs?: number
}

export class RedisCandlePublisher implements CandlePublisher {
  private readonly client: Redis

  constructor(opts: RedisCandlePublisherOptions) {
    this.client = new Redis(opts.url, {
      connectTimeout: opts.connectTimeoutMs ?? 2000,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    })
  }

  async publishCandle(candle: Candle): Promise<void> {
    const stream = `candles:${candle.symbol.toStreamId().toLowerCase()}:${candle.timeframe}`
    await this.client.xadd(stream, "*", "p", JSON.stringify(candle.toJSON()))
  }

  async close(): Promise<void> {
    try {
      await this.client.quit()
    } catch {
      this.client.disconnect()
    }
  }
}
