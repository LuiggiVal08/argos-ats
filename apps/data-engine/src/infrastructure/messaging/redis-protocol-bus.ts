import Redis, { RedisOptions } from "ioredis"
import { Tick } from "../../domain/entities/tick"
import { StreamName } from "../../domain/value-objects/stream-name"
import { MessageBus } from "../../application/ports/message-bus.port"

export interface RedisProtocolBusOptions {
  /** Full URL, e.g. `redis://localhost:6379`. Required. */
  url: string
  /**
   * Optional ioredis options overrides (tls, password, db, ...).
   * Used to support managed brokers (ElastiCache, Upstash, Memurai
   * with auth, etc.) without coupling the application to specific
   * options.
   */
  redisOptions?: Partial<RedisOptions>
  /** Connection timeout in ms. Default 2000. */
  connectTimeoutMs?: number
  /**
   * Whether to lazyConnect. When true, the first publish() triggers
   * connect(). Useful in tests to avoid side effects on construction.
   * Default false.
   */
  lazyConnect?: boolean
}

/**
 * MessageBus adapter for any RESP-compatible broker.
 *
 * Uses ioredis to talk to Redis 7+, Memurai, Dragonfly, Valkey, KeyDB,
 * Garnet, Redict — anything that speaks the RESP protocol.
 *
 * Single-tick XADD per publish(): trading is event-driven, no batching.
 * Each call awaits the broker ack before returning.
 *
 * Connection lifecycle:
 *  - ioredis auto-reconnects on transient failures.
 *  - publish() throws if the connection is down (caller routes to
 *    TickBuffer per spec).
 *  - ping() returns true only if a PING round-trip succeeds.
 */
export class RedisProtocolBus implements MessageBus {
  private readonly client: Redis
  private readonly opts: Required<Omit<RedisProtocolBusOptions, "redisOptions">> & {
    redisOptions?: Partial<RedisOptions>
  }

  constructor(opts: RedisProtocolBusOptions) {
    if (!opts.url) {
      throw new Error("RedisProtocolBus: url is required")
    }
    this.opts = {
      url: opts.url,
      connectTimeoutMs: opts.connectTimeoutMs ?? 2000,
      lazyConnect: opts.lazyConnect ?? false,
      redisOptions: opts.redisOptions,
    }
    this.client = new Redis(this.opts.url, {
      ...(this.opts.redisOptions ?? {}),
      connectTimeout: this.opts.connectTimeoutMs,
      lazyConnect: this.opts.lazyConnect,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    })
  }

  async publish(stream: StreamName, tick: Tick): Promise<void> {
    // The "payload" field carries the JSON-serialized tick.
    // Stream entries are tuples of (field, value); we use a single
    // field "p" for compactness. The full Tick is recoverable via
    // Tick.fromJSON on the consumer side.
    await this.client.xadd(
      stream.toString(),
      "*",
      "p",
      JSON.stringify(tick.toJSON()),
    )
  }

  async publishRaw(stream: StreamName, data: Record<string, unknown>): Promise<void> {
    await this.client.xadd(
      stream.toString(),
      "*",
      "p",
      JSON.stringify(data),
    )
  }

  async subscribe(
    stream: StreamName,
    handler: (tick: Tick) => Promise<void>,
  ): Promise<() => Promise<void>> {
    // XREAD-based pull loop. We use a dedicated connection because
    // ioredis reserves the main connection for commands; subscribers
    // need their own client.
    const sub = new Redis(this.opts.url, {
      ...(this.opts.redisOptions ?? {}),
      connectTimeout: this.opts.connectTimeoutMs,
      maxRetriesPerRequest: 1,
    })
    let stopped = false
    const lastId = "$"

    const loop = async (): Promise<void> => {
      while (!stopped) {
        try {
          // ioredis 5.x types are over-restrictive for variadic xread
          // with BLOCK + COUNT + STREAMS. The runtime supports it, so
          // we cast at the boundary. (The response shape is asserted
          // via `as` immediately below.)
          const res = (await (
            sub as unknown as {
              xread: (...args: Array<string | number>) => Promise<unknown>
            }
          ).xread(
            "BLOCK",
            1000,
            "COUNT",
            100,
            "STREAMS",
            stream.toString(),
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
                const tick = Tick.fromJSON(JSON.parse(raw))
                await handler(tick)
              } catch {
                // Swallow handler errors to keep the loop alive. The
                // application layer decides whether to log or escalate.
              }
            }
          }
        } catch (e) {
          if (stopped) return
          // ioredis throws on disconnect. Sleep and retry.
          await new Promise<void>((r) => setTimeout(r, 100))
        }
      }
    }
    void loop()

    return async () => {
      stopped = true
      try {
        await sub.quit()
      } catch {
        sub.disconnect()
      }
    }
  }

  async ping(): Promise<boolean> {
    try {
      const r = await this.client.ping()
      return r === "PONG"
    } catch {
      return false
    }
  }

  async close(): Promise<void> {
    try {
      await this.client.quit()
    } catch {
      this.client.disconnect()
    }
  }
}
