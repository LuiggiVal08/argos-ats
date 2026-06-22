import {
  Injectable,
  OnModuleDestroy,
  OnModuleInit,
} from "@nestjs/common"
import Redis from "ioredis"
import { sendDiscord } from "./discord-notifier"
import { sendTelegram } from "./telegram-notifier"

const log = (m: string): void => {
  // eslint-disable-next-line no-console
  console.log(m)
}

const STREAM = "notifications:events"

interface NotificationEvent {
  event_type: string
  severity: string
  title: string
  message: string
  symbol?: string
  metadata: Record<string, string>
  timestamp: string
}

/**
 * Consumes the `notifications:events` Redis stream and dispatches
 * events to Telegram and/or Discord webhooks. Runs in a background
 * pull loop (XREAD BLOCK) and shuts down cleanly with the app.
 */
@Injectable()
export class NotificationConsumer implements OnModuleInit, OnModuleDestroy {
  private sub: Redis | null = null
  private stopped = false

  onModuleInit(): void {
    const url = process.env.ARGOS_BROKER_URL
    if (!url) {
      log("[notif] ARGOS_BROKER_URL not set, notification consumer disabled")
      return
    }
    void this.start(url)
  }

  onModuleDestroy(): void {
    this.stopped = true
    if (this.sub) {
      try {
        this.sub.quit()
      } catch {
        console.warn("[notif] consumer quit failed")
        this.sub.disconnect()
      }
    }
  }

  private async start(url: string): Promise<void> {
    this.sub = new Redis(url)
    log(`[notif] consumer started on stream=${STREAM}`)
    let lastId = "$"

    while (!this.stopped) {
      try {
        const res = (await (
          this.sub as unknown as {
            xread: (...args: Array<string | number>) => Promise<unknown>
          }
        ).xread(
          "BLOCK",
          1000,
          "COUNT",
          10,
          "STREAMS",
          STREAM,
          lastId,
        )) as Array<[string, Array<[string, string[]]>]> | null

        if (!res) continue

        for (const [, entries] of res) {
          for (const [id, fields] of entries) {
            (lastId as unknown) = id
            const idx = fields.indexOf("p")
            if (idx === -1) continue
            const raw = fields[idx + 1]
            if (!raw) continue
            try {
              const event: NotificationEvent = JSON.parse(raw)
              await this.dispatch(event)
            } catch (parseErr) {
              log(`[notif] parse error: ${String(parseErr)}`)
            }
          }
        }
      } catch (e) {
        if (this.stopped) return
        log(`[notif] xread error: ${String(e)}`)
        await new Promise<void>((r) => setTimeout(r, 100))
      }
    }
  }

  private async dispatch(event: NotificationEvent): Promise<void> {
    const promises: Array<Promise<void>> = []
    promises.push(sendTelegram(event.title, event.message, event.symbol))
    promises.push(
      sendDiscord({
        event_type: event.event_type,
        severity: event.severity,
        title: event.title,
        message: event.message,
        symbol: event.symbol,
        timestamp: event.timestamp,
      }),
    )
    await Promise.allSettled(promises)
  }
}
