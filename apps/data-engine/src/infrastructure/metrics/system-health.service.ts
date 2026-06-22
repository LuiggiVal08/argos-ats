import Redis from "ioredis"
import { ContractViolationMetrics } from "./contract-violation-metrics"
import { ExecutionMetrics } from "./execution-metrics"
import { StreamLagReport } from "./stream-lag-monitor"

export interface SystemHealthSnapshot {
  service: "data-engine"
  status: "healthy" | "degraded" | "halted"
  contract_violation_rate: number
  avg_execution_latency_ms: number
  stream_lag_ms: StreamLagReport
  uptime: number
  start_time: number
  timestamp: number
}

export class SystemHealthService {
  private readonly client: Redis
  private readonly streamKey: string
  private readonly startTime: number
  private intervalHandle: ReturnType<typeof setInterval> | null = null

  constructor(
    private readonly redisUrl: string,
    private readonly violationMetrics: ContractViolationMetrics,
    private readonly execMetrics: ExecutionMetrics,
    streamKey = "system:metrics",
  ) {
    this.client = new Redis(redisUrl, {
      connectTimeout: 2000,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    })
    this.streamKey = streamKey
    this.startTime = Date.now()
  }

  private status(): "healthy" | "degraded" | "halted" {
    const vRate = this.violationMetrics.totalViolationRate()
    if (vRate > 0.05) return "halted"
    if (vRate > 0.01) return "degraded"
    return "healthy"
  }

  snapshot(lagReport: StreamLagReport): SystemHealthSnapshot {
    return {
      service: "data-engine",
      status: this.status(),
      contract_violation_rate: this.violationMetrics.totalViolationRate(),
      avg_execution_latency_ms: this.execMetrics.avgExecutionLatencyMs(),
      stream_lag_ms: lagReport,
      uptime: Math.floor((Date.now() - this.startTime) / 1000),
      start_time: this.startTime,
      timestamp: Date.now(),
    }
  }

  async publish(lagReport: StreamLagReport): Promise<void> {
    const snap = this.snapshot(lagReport)
    try {
      await this.client.xadd(this.streamKey, "*", "p", JSON.stringify(snap))
    } catch {
      console.warn("[health] snapshot publish failed")
    }
  }

  async start(lagReport: () => StreamLagReport, intervalMs = 30_000): Promise<void> {
    if (this.intervalHandle) return
    this.intervalHandle = setInterval(async () => {
      try {
        await this.publish(lagReport())
      } catch {
        console.warn("[health] periodic publish failed")
      }
    }, intervalMs)
    console.warn(`[system_health] service started interval=${intervalMs}ms`)
  }

  stop(): void {
    if (this.intervalHandle) {
      clearInterval(this.intervalHandle)
      this.intervalHandle = null
    }
  }

  async close(): Promise<void> {
    this.stop()
    try {
      await this.client.quit()
    } catch {
      console.warn("[health] quit failed")
      this.client.disconnect()
    }
  }
}
