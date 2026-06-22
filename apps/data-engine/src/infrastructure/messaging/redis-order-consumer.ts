import Redis from "ioredis"
import { validateOrder } from "../../contracts"
import {
  buildErrorPayload,
  emitError,
  formatValidationErrors,
} from "./emit-error"

export type OrderHandler = (payload: Record<string, unknown>) => Promise<void>

export interface RedisOrderConsumerOptions {
  url: string
  streamKey?: string
  connectTimeoutMs?: number
}

export class RedisOrderConsumer {
  private readonly client: Redis
  private readonly streamKey: string
  private stopped = false
  private parseErrorCount = 0
  private handlerErrorCount = 0

  constructor(opts: RedisOrderConsumerOptions) {
    this.client = new Redis(opts.url, {
      connectTimeout: opts.connectTimeoutMs ?? 2000,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    })
    this.streamKey = opts.streamKey ?? "orders:execution"
  }

  async start(handler: OrderHandler): Promise<void> {
    let lastId = "$"
    while (!this.stopped) {
      try {
        const res = (await (
          this.client as unknown as {
            xread: (...args: Array<string | number>) => Promise<unknown>
          }
        ).xread("BLOCK", 1000, "COUNT", 10, "STREAMS", this.streamKey, lastId)) as
          | Array<[string, Array<[string, string[]]>]>
          | null

        if (!res) continue

        for (const [, entries] of res) {
          for (const [id, fields] of entries) {
            lastId = id
            const idx = fields.indexOf("p")
            if (idx === -1) continue
            const raw = fields[idx + 1]
            if (!raw) continue

            let payload: Record<string, unknown>
            try {
              payload = JSON.parse(raw)
            } catch {
              this.parseErrorCount++
              if (this.parseErrorCount % 50 === 0) {
                console.warn(`[order-consumer] parse errors: ${this.parseErrorCount}`)
              }
              continue
            }

            const result = validateOrder(payload)
            if (!result.ok) {
              await emitError(
                this.client,
                buildErrorPayload(
                  "ORDER_VALIDATION_FAILED",
                  `Invalid order payload dropped: ${formatValidationErrors(result.errors)}`,
                  "WARN",
                  { raw_payload: raw, stream: this.streamKey },
                ),
              )
              continue
            }

            try {
              await handler(payload)
            } catch {
              this.handlerErrorCount++
              if (this.handlerErrorCount % 50 === 0) {
                console.warn(`[order-consumer] handler errors: ${this.handlerErrorCount}`)
              }
            }
          }
        }
      } catch (e) {
        if (this.stopped) return
        await new Promise<void>((r) => setTimeout(r, 100))
      }
    }
  }

  async stop(): Promise<void> {
    this.stopped = true
    try {
      await this.client.quit()
    } catch {
      console.warn("[order-consumer] quit failed")
      this.client.disconnect()
    }
  }
}
