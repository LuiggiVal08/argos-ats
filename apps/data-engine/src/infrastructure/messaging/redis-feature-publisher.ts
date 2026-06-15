import Redis from "ioredis"
import { FeatureVector } from "../../domain/entities/feature-vector"
import { FeaturePublisher } from "../../application/ports/feature-publisher.port"

export interface RedisFeaturePublisherOptions {
  url: string
  streamPrefix?: string
  connectTimeoutMs?: number
}

export class RedisFeaturePublisher implements FeaturePublisher {
  private readonly client: Redis
  private readonly prefix: string

  constructor(opts: RedisFeaturePublisherOptions) {
    this.client = new Redis(opts.url, {
      connectTimeout: opts.connectTimeoutMs ?? 2000,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    })
    this.prefix = opts.streamPrefix ?? "features:"
  }

  async publish(vector: FeatureVector): Promise<void> {
    const stream = `${this.prefix}${vector.symbol.toStreamId().toLowerCase()}:${vector.timeframe}`
    await this.client.xadd(stream, "*", "p", JSON.stringify(vector.toJSON()))
  }

  async close(): Promise<void> {
    try {
      await this.client.quit()
    } catch {
      this.client.disconnect()
    }
  }
}
