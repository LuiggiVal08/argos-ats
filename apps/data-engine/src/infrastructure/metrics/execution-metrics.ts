import { randomUUID } from "crypto"
import Redis from "ioredis"

export interface ExecutionSample {
  orderId: string
  symbol: string
  side: string
  executionLatencyMs: number
  fillLatencyMs?: number
  slippage?: number
  success: boolean
  retryCount: number
  timestamp: number
}

export class ExecutionMetrics {
  private readonly samples: ExecutionSample[] = []
  private readonly maxSamples = 500
  private readonly client: Redis | null = null
  private retryCount = 0

  constructor(redisUrl?: string) {
    if (redisUrl) {
      this.client = new Redis(redisUrl, {
        connectTimeout: 2000,
        maxRetriesPerRequest: 1,
        enableOfflineQueue: false,
      })
    }
  }

  recordExecution(sample: ExecutionSample): void {
    this.samples.push(sample)
    if (this.samples.length > this.maxSamples) this.samples.shift()
    this.retryCount += sample.retryCount

    console.warn(`[execution_metric] order=${sample.orderId} symbol=${sample.symbol} side=${sample.side} latency=${sample.executionLatencyMs}ms success=${sample.success} retries=${sample.retryCount}`)
  }

  avgExecutionLatencyMs(): number {
    if (this.samples.length === 0) return 0
    const total = this.samples.reduce((a, s) => a + s.executionLatencyMs, 0)
    return total / this.samples.length
  }

  avgFillLatencyMs(): number {
    const withFill = this.samples.filter((s) => s.fillLatencyMs !== undefined)
    if (withFill.length === 0) return 0
    const total = withFill.reduce((a, s) => a + (s.fillLatencyMs ?? 0), 0)
    return total / withFill.length
  }

  successRate(): number {
    if (this.samples.length === 0) return 1
    const ok = this.samples.filter((s) => s.success).length
    return ok / this.samples.length
  }

  totalRetryCount(): number {
    return this.retryCount
  }

  avgSlippage(): number {
    const withSlip = this.samples.filter((s) => s.slippage !== undefined)
    if (withSlip.length === 0) return 0
    const total = withSlip.reduce((a, s) => a + (s.slippage ?? 0), 0)
    return total / withSlip.length
  }

  async publishSnapshot(): Promise<void> {
    if (!this.client) return

    const payload = {
      metric_id: randomUUID(),
      service: "data-engine",
      metric: "execution",
      avg_execution_latency_ms: this.avgExecutionLatencyMs(),
      avg_fill_latency_ms: this.avgFillLatencyMs(),
      success_rate: this.successRate(),
      total_retries: this.totalRetryCount(),
      avg_slippage: this.avgSlippage(),
      timestamp: Date.now(),
    }
    try {
      await this.client.xadd("system:metrics", "*", "p", JSON.stringify(payload))
    } catch {
      console.warn("[metrics] execution metrics publish failed")
    }
  }

  async close(): Promise<void> {
    if (this.client) {
      try { await this.client.quit() } catch { console.warn("[metrics] execution quit failed"); this.client.disconnect() }
    }
  }
}
