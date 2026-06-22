import { Tick } from "../../domain/entities/tick"
import { StreamName } from "../../domain/value-objects/stream-name"
import { MessageBus } from "../ports/message-bus.port"
import { TickBuffer } from "../ports/tick-buffer.port"

export interface FlushResult {
  drained: number
  published: number
  reBuffered: number
}

export class FlushBufferUseCase {
  constructor(
    private readonly bus: MessageBus,
    private readonly buffer: TickBuffer,
    private readonly streamPrefix: string,
  ) {}

  async execute(): Promise<FlushResult> {
    const ticks: Tick[] = await this.buffer.drain()
    let published = 0
    let reBuffered = 0
    let failed = false
    for (let i = 0; i < ticks.length; i++) {
      const t = ticks[i]!
      if (failed) {
        await this.buffer.push(t)
        reBuffered++
        continue
      }
      try {
        const stream = StreamName.forTicks(t.symbol, this.streamPrefix)
        await this.bus.publish(stream, t)
        published++
      } catch {
        failed = true
        await this.buffer.push(t)
        reBuffered++
        console.warn(`[flush] publish failed for tick ${t.tradeId}`)
      }
    }
    return { drained: ticks.length, published, reBuffered }
  }
}
