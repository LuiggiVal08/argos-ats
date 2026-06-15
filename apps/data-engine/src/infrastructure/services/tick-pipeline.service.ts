import {
  Inject,
  Injectable,
  OnModuleDestroy,
  OnModuleInit,
} from "@nestjs/common"
import { ExchangeGateway } from "../../application/ports/exchange-gateway.port"
import { HealthMonitor } from "../../application/ports/health-monitor.port"
import { IngestTickUseCase } from "../../application/use-cases/ingest-tick.usecase"
import { BufferTickUseCase } from "../../application/use-cases/buffer-tick.usecase"
import { FlushBufferUseCase } from "../../application/use-cases/flush-buffer.usecase"
import { HealthMonitorUseCase } from "../../application/use-cases/health-monitor.usecase"
import { StreamName } from "../../domain/value-objects/stream-name"
import { InMemoryTickBuffer } from "../messaging/in-memory-tick-buffer"
import {
  EXCHANGE_GATEWAY,
  HEALTH_MONITOR,
  TICK_BUFFER,
} from "../config/tokens"

const log = (m: string): void => {
  // eslint-disable-next-line no-console
  console.log(m)
}

const PREFIX = process.env.STREAM_PREFIX ?? "ticks:"

@Injectable()
export class TickPipelineService implements OnModuleInit, OnModuleDestroy {
  private pollHandle: NodeJS.Timeout | null = null

  constructor(
    @Inject(EXCHANGE_GATEWAY)
    private readonly exchange: ExchangeGateway,
    private readonly ingest: IngestTickUseCase,
    private readonly bufferUseCase: BufferTickUseCase,
    @Inject(HEALTH_MONITOR)
    private readonly monitor: HealthMonitor,
    private readonly monitorUc: HealthMonitorUseCase,
    @Inject(TICK_BUFFER)
    private readonly tickBuffer: InMemoryTickBuffer,
    private readonly flush: FlushBufferUseCase,
  ) {}

  async onModuleInit(): Promise<void> {
    log(`[pipeline] starting. buffer=${this.tickBuffer.capacity()} monitor=bus`)
    this.monitor.start()
    this.pollHandle = setInterval(() => {
      void this.monitorUc.tick()
    }, 1000)

    try {
      await this.exchange.start(async (tick) => {
        const stream = StreamName.forTicks(tick.symbol, PREFIX)
        const r = await this.ingest.execute(tick, stream)
        if (r.buffered) {
          log(`[pipeline] tick ${tick.tradeId} buffered (size=${this.tickBuffer.size()})`)
        }
      })
    } catch (err) {
      log(`[pipeline] exchange start failed: ${(err as Error).message} — retrying in background`)
    }
  }

  async onModuleDestroy(): Promise<void> {
    log("[pipeline] shutting down")
    if (this.pollHandle) {
      clearInterval(this.pollHandle)
      this.pollHandle = null
    }

    const drained = this.tickBuffer.size()
    if (drained > 0) {
      log(`[pipeline] draining ${drained} buffered ticks before shutdown`)
      try { await this.flush.execute() } catch (e) {
        log(`[pipeline] drain error: ${(e as Error).message}`)
      }
    }

    await this.monitor.stop()
    await this.exchange.close()
  }
}
