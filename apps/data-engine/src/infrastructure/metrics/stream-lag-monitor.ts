import { randomUUID } from "crypto"
import Redis from "ioredis"

export interface LagSample {
  stream: string
  producerTimestamp: number
  consumerTimestamp: number
  lagMs: number
}

export interface StreamLagReport {
  candles: number
  signals: number
  orders: number
  fills: number
}

export class StreamLagMonitor {
  private readonly samples: Map<string, number[]> = new Map()
  private readonly maxSamples: number

  constructor(maxSamples = 100) {
    this.maxSamples = maxSamples
  }

  recordLag(stream: string, producerTs: number, consumerTs: number): void {
    const lag = Math.max(0, consumerTs - producerTs)
    const buf = this.samples.get(stream) ?? []
    buf.push(lag)
    if (buf.length > this.maxSamples) buf.shift()
    this.samples.set(stream, buf)
  }

  avgLag(stream: string): number {
    const buf = this.samples.get(stream)
    if (!buf || buf.length === 0) return 0
    return buf.reduce((a, b) => a + b, 0) / buf.length
  }

  report(): StreamLagReport {
    return {
      candles: this.avgLag("candles"),
      signals: this.avgLag("signals"),
      orders: this.avgLag("orders"),
      fills: this.avgLag("fills"),
    }
  }

  p99Lag(stream: string): number {
    const buf = this.samples.get(stream)
    if (!buf || buf.length === 0) return 0
    const sorted = [...buf].sort((a, b) => a - b)
    const idx = Math.ceil(sorted.length * 0.99) - 1
    return sorted[Math.max(0, idx)]
  }

  reset(): void {
    this.samples.clear()
  }
}

export class RedisLagPublisher {
  private readonly client: Redis
  private readonly streamKey: string

  constructor(redisUrl: string, streamKey = "system:latency") {
    this.client = new Redis(redisUrl, {
      connectTimeout: 2000,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    })
    this.streamKey = streamKey
  }

  async publishLag(lagReport: StreamLagReport): Promise<void> {
    const payload = {
      metric_id: randomUUID(),
      service: "data-engine",
      streams: lagReport,
      timestamp: Date.now(),
    }
    try {
      await this.client.xadd(this.streamKey, "*", "p", JSON.stringify(payload))
    } catch {
      console.warn("[lag] lag publish failed")
    }
  }

  async close(): Promise<void> {
    try {
      await this.client.quit()
    } catch {
      console.warn("[lag] quit failed")
      this.client.disconnect()
    }
  }
}
