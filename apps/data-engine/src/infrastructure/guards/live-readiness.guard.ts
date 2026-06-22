export interface LiveReadinessReport {
  live_ready: boolean
  blockers: string[]
  warnings: string[]
}

export interface LiveReadinessInput {
  contractViolationRate: number
  streamLagMs: {
    candles: number
    signals: number
    orders: number
    fills: number
  }
  avgExecutionLatencyMs: number
  executionSuccessRate: number
  uptimeSeconds: number
  heartbeatReceived: boolean
}

const DEFAULT_LAG_THRESHOLD_MS = {
  candles: 500,
  signals: 1000,
  orders: 500,
  fills: 500,
}

const DEFAULT_VIOLATION_RATE_MAX = 0.01
const DEFAULT_EXECUTION_LATENCY_MAX_MS = 500
const DEFAULT_MIN_UPTIME_SECONDS = 60

export class LiveReadinessGuard {
  private readonly lagThresholds: typeof DEFAULT_LAG_THRESHOLD_MS
  private readonly violationRateMax: number
  private readonly executionLatencyMaxMs: number
  private readonly minUptimeSeconds: number

  constructor(opts?: {
    lagThresholds?: Partial<typeof DEFAULT_LAG_THRESHOLD_MS>
    violationRateMax?: number
    executionLatencyMaxMs?: number
    minUptimeSeconds?: number
  }) {
    this.lagThresholds = { ...DEFAULT_LAG_THRESHOLD_MS, ...opts?.lagThresholds }
    this.violationRateMax = opts?.violationRateMax ?? DEFAULT_VIOLATION_RATE_MAX
    this.executionLatencyMaxMs = opts?.executionLatencyMaxMs ?? DEFAULT_EXECUTION_LATENCY_MAX_MS
    this.minUptimeSeconds = opts?.minUptimeSeconds ?? DEFAULT_MIN_UPTIME_SECONDS
  }

  check(input: LiveReadinessInput): LiveReadinessReport {
    const blockers: string[] = []
    const warnings: string[] = []

    if (!input.heartbeatReceived) {
      blockers.push("missing_heartbeat")
    }

    for (const [stream, threshold] of Object.entries(this.lagThresholds)) {
      const actual = input.streamLagMs[stream as keyof typeof input.streamLagMs]
      if (actual > threshold) {
        blockers.push(`stream_lag_exceeded:${stream}=${actual}ms>${threshold}ms`)
      }
    }

    if (input.contractViolationRate > this.violationRateMax) {
      blockers.push(
        `contract_violation_rate_exceeded:${(input.contractViolationRate * 100).toFixed(2)}%>${(this.violationRateMax * 100).toFixed(2)}%`,
      )
    }

    if (input.avgExecutionLatencyMs > this.executionLatencyMaxMs) {
      blockers.push(
        `execution_latency_exceeded:${input.avgExecutionLatencyMs}ms>${this.executionLatencyMaxMs}ms`,
      )
    }

    if (input.executionSuccessRate < 0.5 && input.uptimeSeconds > 300) {
      blockers.push(
        `execution_success_rate_low:${(input.executionSuccessRate * 100).toFixed(1)}%`,
      )
    }

    if (input.uptimeSeconds < this.minUptimeSeconds) {
      warnings.push(`uptime_below_minimum:${input.uptimeSeconds}s<${this.minUptimeSeconds}s`)
    }

    return {
      live_ready: blockers.length === 0,
      blockers,
      warnings,
    }
  }
}
