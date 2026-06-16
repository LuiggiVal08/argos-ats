import {
  Inject,
  Injectable,
  OnModuleDestroy,
  OnModuleInit,
} from "@nestjs/common"
import { ExchangeGateway } from "../../application/ports/exchange-gateway.port"
import { HealthMonitor } from "../../application/ports/health-monitor.port"
import { IngestTickUseCase } from "../../application/use-cases/ingest-tick.usecase"
import { IngestAdditionalDataUseCase } from "../../application/use-cases/ingest-additional-data.usecase"
import { BufferTickUseCase } from "../../application/use-cases/buffer-tick.usecase"
import { FlushBufferUseCase } from "../../application/use-cases/flush-buffer.usecase"
import { HealthMonitorUseCase } from "../../application/use-cases/health-monitor.usecase"
import { BinanceRestPoller } from "../messaging/binance-rest-poller"
import { StreamName } from "../../domain/value-objects/stream-name"
import { InMemoryTickBuffer } from "../messaging/in-memory-tick-buffer"
import {
  EXCHANGE_GATEWAY,
  HEALTH_MONITOR,
  TICK_BUFFER,
  SYMBOLS,
} from "../config/tokens"
import { Symbol as SymbolVo } from "../../domain/value-objects/symbol"

const log = (m: string): void => {
  // eslint-disable-next-line no-console
  console.log(m)
}

const PREFIX = process.env.STREAM_PREFIX ?? "ticks:"

@Injectable()
export class TickPipelineService implements OnModuleInit, OnModuleDestroy {
  private pollHandle: NodeJS.Timeout | null = null
  private restPoller: BinanceRestPoller | null = null

  constructor(
    @Inject(EXCHANGE_GATEWAY)
    private readonly exchange: ExchangeGateway,
    private readonly ingest: IngestTickUseCase,
    private readonly ingestAdditional: IngestAdditionalDataUseCase,
    private readonly bufferUseCase: BufferTickUseCase,
    @Inject(HEALTH_MONITOR)
    private readonly monitor: HealthMonitor,
    private readonly monitorUc: HealthMonitorUseCase,
    @Inject(TICK_BUFFER)
    private readonly tickBuffer: InMemoryTickBuffer,
    private readonly flush: FlushBufferUseCase,
    @Inject(SYMBOLS)
    private readonly symbols: SymbolVo[],
  ) {}

  async onModuleInit(): Promise<void> {
    log(`[pipeline] starting. buffer=${this.tickBuffer.capacity()} monitor=bus`)
    this.monitor.start()
    this.pollHandle = setInterval(() => {
      void this.monitorUc.tick()
    }, 1000)

    const onTick = async (tick: import("../../domain/entities/tick").Tick): Promise<void> => {
      const stream = StreamName.forTicks(tick.symbol, PREFIX)
      const r = await this.ingest.execute(tick, stream)
      if (r.buffered) {
        log(`[pipeline] tick ${tick.tradeId} buffered (size=${this.tickBuffer.size()})`)
      }
    }

    const onFundingRate = async (fr: import("../../domain/value-objects/funding-rate").FundingRate): Promise<void> => {
      await this.ingestAdditional.publishFundingRate(fr)
    }

    const onAggTrade = async (trade: import("../../domain/value-objects/agg-trade").AggTrade): Promise<void> => {
      await this.ingestAdditional.publishAggTrade(trade)
    }

    try {
      await this.exchange.start(onTick, onFundingRate, onAggTrade)
    } catch (err) {
      log(`[pipeline] exchange start failed: ${(err as Error).message} — retrying in background`)
    }

    const symbolIds = this.symbols.map((s) => s.toStreamId())
    this.restPoller = new BinanceRestPoller({
      symbols: symbolIds,
      pollIntervalMs: 60_000,
      logger: log,
      onOpenInterest: async (oi) => {
        await this.ingestAdditional.publishOpenInterest(oi)
      },
    })
    this.restPoller.start()
    log("[pipeline] OI poller started")
  }

  async onModuleDestroy(): Promise<void> {
    log("[pipeline] shutting down")
    if (this.restPoller) this.restPoller.stop()
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
