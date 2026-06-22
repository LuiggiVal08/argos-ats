import Redis from "ioredis"
import { validateFill } from "../../contracts"
import {
  buildErrorPayload,
  emitError,
  formatValidationErrors,
} from "./emit-error"

export interface RedisFillPublisherOptions {
  url: string
  streamKey?: string
  connectTimeoutMs?: number
}

export class RedisFillPublisher {
  private readonly client: Redis
  private readonly streamKey: string

  constructor(opts: RedisFillPublisherOptions) {
    this.client = new Redis(opts.url, {
      connectTimeout: opts.connectTimeoutMs ?? 2000,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    })
    this.streamKey = opts.streamKey ?? "fills:execution"
  }

  async publishFill(payload: Record<string, unknown>): Promise<void> {
    const result = validateFill(payload)
    if (!result.ok) {
      await emitError(
        this.client,
        buildErrorPayload(
          "FILL_VALIDATION_FAILED",
          `Invalid fill payload rejected: ${formatValidationErrors(result.errors)}`,
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
      console.warn("[fill-publisher] quit failed")
      this.client.disconnect()
    }
  }
}
