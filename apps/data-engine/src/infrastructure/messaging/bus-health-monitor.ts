import { HealthMonitor } from "../../application/ports/health-monitor.port"
import { MessageBus } from "../../application/ports/message-bus.port"

/**
 * HealthMonitor adapter that probes the broker via MessageBus.ping().
 *
 * Tracks two booleans internally:
 *  - isHealthy: last observed state
 *  - consecutiveFailures: how many probes in a row have failed
 *
 * The use case (HealthMonitorUseCase) is what decides what to do
 * with the state (cutoff timer, flush on recovery, close WS).
 */
export class BusHealthMonitor implements HealthMonitor {
  private running = false
  private healthy = true
  private timer: NodeJS.Timeout | null = null
  private readonly intervalMs: number

  constructor(
    private readonly bus: MessageBus,
    opts: { intervalMs?: number; timeoutMs?: number } = {},
  ) {
    this.intervalMs = opts.intervalMs ?? 1000
  }

  start(): void {
    if (this.running) return
    this.running = true
    this.healthy = true
    this.timer = setInterval(() => {
      void this.probe()
    }, this.intervalMs)
  }

  private async probe(): Promise<void> {
    if (!this.running) return
    let ok = false
    try {
      ok = await this.bus.ping()
    } catch {
      ok = false
      console.warn("[bus-health] ping probe failed")
    }
    this.healthy = ok
  }

  isHealthy(): boolean {
    return this.healthy
  }

  async stop(): Promise<void> {
    this.running = false
    if (this.timer) {
      clearInterval(this.timer)
      this.timer = null
    }
  }
}
