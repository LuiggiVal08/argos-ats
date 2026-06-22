import { Tick } from "../../domain/entities/tick"
import { StreamName } from "../../domain/value-objects/stream-name"
import { HistoricalEvent } from "../../domain/entities/historical-event"
import { MessageBus } from "../ports/message-bus.port"
import { TickBuffer } from "../ports/tick-buffer.port"
import { EventStore } from "../ports/event-store.port"

function tickToEvent(tick: Tick): HistoricalEvent {
  const j = tick.toJSON()
  return { kind: "tick", data: j }
}

export class IngestTickUseCase {
  constructor(
    private readonly bus: MessageBus,
    private readonly buffer: TickBuffer,
    private readonly defaultStream: StreamName,
    private readonly store: EventStore,
  ) {}

  private publishFailCount = 0

  async execute(
    tick: Tick,
    stream?: StreamName,
  ): Promise<{ published: boolean; buffered: boolean }> {
    const target = stream ?? this.defaultStream
    try {
      await this.store.store(tickToEvent(tick))
    } catch {
      console.warn("[ingest] event store write failed; continuing to publish")
    }
    try {
      await this.bus.publish(target, tick)
      return { published: true, buffered: false }
    } catch {
      await this.buffer.push(tick)
      this.publishFailCount++
      if (this.publishFailCount % 100 === 0) {
        console.warn(`[ingest] publish failed (${this.publishFailCount} total)`)
      }
      return { published: false, buffered: true }
    }
  }
}
