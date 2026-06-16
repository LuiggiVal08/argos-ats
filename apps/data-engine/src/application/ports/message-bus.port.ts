import { StreamName } from "../../domain/value-objects/stream-name"
import { Tick } from "../../domain/entities/tick"

/**
 * Generic message bus port.
 *
 * Broker-agnostic. The adapter (e.g. RedisProtocolBus) implements this
 * against a specific protocol family (RESP, AMQP, NATS, ...).
 *
 * The application layer depends only on this interface — never on
 * ioredis, nats.js, amqplib, etc.
 */
export interface MessageBus {
  /** Publish a tick event to a stream. Throws on broker error. */
  publish(stream: StreamName, tick: Tick): Promise<void>

  /** Publish arbitrary JSON-serializable data to a stream. */
  publishRaw(stream: StreamName, data: Record<string, unknown>): Promise<void>

  /**
   * Subscribe to a stream. The handler is invoked for each event.
   * Returns an unsubscribe function.
   *
   * Implementations should NOT block the caller; the handler runs
   * asynchronously and the unsubscribe is best-effort.
   */
  subscribe(
    stream: StreamName,
    handler: (tick: Tick) => Promise<void>,
  ): Promise<() => Promise<void>>

  /** Lightweight liveness probe. Returns true if the broker is reachable. */
  ping(): Promise<boolean>

  /** Release all resources (sockets, subscriptions, connections). */
  close(): Promise<void>
}
