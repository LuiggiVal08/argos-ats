import Redis from "ioredis"
import { validateError } from "../../contracts"
import { ErrorPayload } from "./emit-error"

export interface RedisErrorPublisherOptions {
  url: string
  streamKey?: string
  connectTimeoutMs?: number
}

export class RedisErrorPublisher {
  private readonly client: Redis
  private readonly streamKey: string

  constructor(opts: RedisErrorPublisherOptions) {
    this.client = new Redis(opts.url, {
      connectTimeout: opts.connectTimeoutMs ?? 2000,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    })
    this.streamKey = opts.streamKey ?? "system:errors"
  }

  async publishError(payload: ErrorPayload): Promise<void> {
    const result = validateError(payload as unknown as Record<string, unknown>)
    if (!result.ok) {
      return
    }

    try {
      await this.client.xadd(this.streamKey, "*", "p", JSON.stringify(payload))
    } catch {
      console.warn("[error-publisher] error publish failed")
    }
  }

  async close(): Promise<void> {
    try {
      await this.client.quit()
    } catch {
      console.warn("[error-publisher] quit failed")
      this.client.disconnect()
    }
  }
}
