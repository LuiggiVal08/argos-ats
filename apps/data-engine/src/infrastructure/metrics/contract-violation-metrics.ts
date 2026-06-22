import { randomUUID } from "crypto"
import Redis from "ioredis"

export interface ViolationSample {
  stream: string
  field: string
  rule: string
  timestamp: number
}

export class ContractViolationMetrics {
  private totalProcessed = 0
  private invalidByStream: Map<string, number> = new Map()
  private readonly violations: ViolationSample[] = []
  private readonly maxSamples = 500
  private readonly client: Redis | null = null

  constructor(redisUrl?: string) {
    if (redisUrl) {
      this.client = new Redis(redisUrl, {
        connectTimeout: 2000,
        maxRetriesPerRequest: 1,
        enableOfflineQueue: false,
      })
    }
  }

  recordProcessed(_stream: string): void {
    this.totalProcessed++
  }

  recordViolation(stream: string, field: string, rule: string): void {
    this.invalidByStream.set(stream, (this.invalidByStream.get(stream) ?? 0) + 1)
    const sample: ViolationSample = { stream, field, rule, timestamp: Date.now() }
    this.violations.push(sample)
    if (this.violations.length > this.maxSamples) this.violations.shift()

    console.warn(`[contract_violation] stream=${stream} field=${field} rule=${rule}`)
  }

  violationCount(stream: string): number {
    return this.invalidByStream.get(stream) ?? 0
  }

  violationRate(stream: string): number {
    const invalid = this.violationCount(stream)
    if (invalid === 0) return 0
    return invalid / Math.max(1, this.totalProcessed)
  }

  totalViolationRate(): number {
    if (this.totalProcessed === 0) return 0
    let totalInvalid = 0
    for (const c of this.invalidByStream.values()) totalInvalid += c
    return totalInvalid / this.totalProcessed
  }

  getTotalProcessed(): number {
    return this.totalProcessed
  }

  getRecentViolations(n = 10): ViolationSample[] {
    return this.violations.slice(-n)
  }

  async publishSnapshot(): Promise<void> {
    if (!this.client) return

    const payload = {
      metric_id: randomUUID(),
      service: "data-engine",
      metric: "contract_violations",
      total_processed: this.totalProcessed,
      total_invalid_candles: this.violationCount("candles"),
      total_invalid_orders: this.violationCount("orders"),
      total_invalid_fills: this.violationCount("fills"),
      violation_rate: this.totalViolationRate(),
      timestamp: Date.now(),
    }
    try {
      await this.client.xadd("system:contract_violations", "*", "p", JSON.stringify(payload))
    } catch {
      console.warn("[metrics] contract_violations publish failed")
    }
  }

  async close(): Promise<void> {
    if (this.client) {
      try { await this.client.quit() } catch { console.warn("[metrics] contract_violations quit failed"); this.client.disconnect() }
    }
  }
}
