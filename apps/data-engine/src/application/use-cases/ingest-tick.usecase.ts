import { Tick } from "../../domain/entities/tick"
import { StreamName } from "../../domain/value-objects/stream-name"
import { MessageBus } from "../ports/message-bus.port"
import { TickBuffer } from "../ports/tick-buffer.port"

export class IngestTickUseCase {
  constructor(
    private readonly bus: MessageBus,
    private readonly buffer: TickBuffer,
    private readonly defaultStream: StreamName,
  ) {}

  async execute(
    tick: Tick,
    stream?: StreamName,
  ): Promise<{ published: boolean; buffered: boolean }> {
    const target = stream ?? this.defaultStream
    try {
      await this.bus.publish(target, tick)
      return { published: true, buffered: false }
    } catch {
      await this.buffer.push(tick)
      return { published: false, buffered: true }
    }
  }
}
