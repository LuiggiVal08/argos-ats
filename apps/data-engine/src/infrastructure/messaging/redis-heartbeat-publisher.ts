import Redis from "ioredis"
import { validateHeartbeat } from "../../contracts"
import {
  buildErrorPayload,
  emitError,
  formatValidationErrors,
} from "./emit-error"

export interface RedisHeartbeatPublisherOptions {
  url: string
  streamKey?: string
  connectTimeoutMs?: number
}

export class RedisHeartbeatPublisher {
  private readonly client: Redis
  private readonly streamKey: string

  constructor(opts: RedisHeartbeatPublisherOptions) {
    this.client = new Redis(opts.url, {
      connectTimeout: opts.connectTimeoutMs ?? 2000,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    })
    this.streamKey = opts.streamKey ?? "system:heartbeat"
  }

  async publishHeartbeat(payload: Record<string, unknown>): Promise<void> {
    const result = validateHeartbeat(payload)
    if (!result.ok) {
      await emitError(
        this.client,
        buildErrorPayload(
          "HEARTBEAT_VALIDATION_FAILED",
          `Invalid heartbeat payload rejected: ${formatValidationErrors(result.errors)}`,
          "ERROR",
          { payload },
        ),
      )
      return
    }

    await this.client.xadd(this.streamKey, "*", "p", JSON.stringify(payload))
  }

  async close(): Promise<void> {
    try {
      await this.client.quit()
    } catch {
      console.warn("[heartbeat] quit failed")
      this.client.disconnect()
    }
  }
}
