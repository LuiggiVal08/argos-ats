import { Module, Provider } from "@nestjs/common"
import { ConfigModule } from "@nestjs/config"
import { HealthController } from "./infrastructure/http/health.controller"
import { HealthControllerBus } from "./infrastructure/http/health-bus.controller"
import {
  BUS,
  EXCHANGE_GATEWAY,
  HEALTH_MONITOR,
  STREAM_NAME,
  TICK_BUFFER,
  CANDLE_STORE,
  CANDLE_PUBLISHER,
  FEATURE_CALCULATOR,
  FEATURE_PUBLISHER,
  EVENT_STORE,
} from "./infrastructure/config/tokens"
import { RedisProtocolBus } from "./infrastructure/messaging/redis-protocol-bus"
import { createExchangeAdapter } from "./infrastructure/messaging/exchange-adapter.factory"
import { InMemoryTickBuffer } from "./infrastructure/messaging/in-memory-tick-buffer"
import { BusHealthMonitor } from "./infrastructure/messaging/bus-health-monitor"
import { InMemoryCandleStore } from "./infrastructure/messaging/in-memory-candle-store"
import { RedisCandlePublisher } from "./infrastructure/messaging/redis-candle-publisher"
import { Symbol } from "./domain/value-objects/symbol"
import { StreamName } from "./domain/value-objects/stream-name"
import { IngestTickUseCase } from "./application/use-cases/ingest-tick.usecase"
import { BufferTickUseCase } from "./application/use-cases/buffer-tick.usecase"
import { FlushBufferUseCase } from "./application/use-cases/flush-buffer.usecase"
import { HealthMonitorUseCase } from "./application/use-cases/health-monitor.usecase"
import { BuildCandlesUseCase } from "./application/use-cases/build-candles.usecase"
import { CalculateFeaturesUseCase } from "./application/use-cases/calculate-features.usecase"
import { NotificationConsumer } from "./infrastructure/notification/notification-consumer"
import { TickPipelineService } from "./infrastructure/services/tick-pipeline.service"
import { CandlePipelineService } from "./infrastructure/services/candle-pipeline.service"
import { FeaturePipelineService } from "./infrastructure/services/feature-pipeline.service"
import { TechnicalIndicatorCalculator } from "./infrastructure/indicators/technical-indicator-calculator"
import { RedisFeaturePublisher } from "./infrastructure/messaging/redis-feature-publisher"
import { EventStore } from "./application/ports/event-store.port"
import { ExchangeGateway } from "./application/ports/exchange-gateway.port"
import { FileEventStore } from "./infrastructure/storage/file-event-store"
import { HistoricalPipelineService } from "./infrastructure/services/historical-pipeline.service"
import { ReplayMarketUseCase } from "./application/use-cases/replay-market.usecase"

const log = (m: string): void => {
  // eslint-disable-next-line no-console
  console.log(m)
}

const streamNameProvider: Provider = {
  provide: STREAM_NAME,
  useFactory: (): StreamName => {
    const symbol = process.env.SYMBOL ?? "BTC/USDT"
    const prefix = process.env.STREAM_PREFIX ?? "ticks:"
    return StreamName.forTicks(Symbol.parse(symbol), prefix)
  },
}

const busProvider: Provider = {
  provide: BUS,
  useFactory: (): RedisProtocolBus => {
    const url = process.env.ARGOS_BROKER_URL
    if (!url) {
      throw new Error(
        "AppModule: ARGOS_BROKER_URL is required (set in .env or environment)",
      )
    }
    return new RedisProtocolBus({ url })
  },
}

const tickBufferProvider: Provider = {
  provide: TICK_BUFFER,
  useFactory: (): InMemoryTickBuffer => {
    const cap = Number(process.env.TICK_BUFFER_CAP ?? 100)
    return new InMemoryTickBuffer(cap)
  },
}

const exchangeProvider: Provider = {
  provide: EXCHANGE_GATEWAY,
  useFactory: (): ExchangeGateway =>
    createExchangeAdapter(log),
}

const healthMonitorProvider: Provider = {
  provide: HEALTH_MONITOR,
  inject: [BUS],
  useFactory: (bus: RedisProtocolBus): BusHealthMonitor =>
    new BusHealthMonitor(bus, { intervalMs: 1000 }),
}

const ingestProvider: Provider = {
  provide: IngestTickUseCase,
  inject: [BUS, TICK_BUFFER, STREAM_NAME],
  useFactory: (
    bus: RedisProtocolBus,
    buffer: InMemoryTickBuffer,
    stream: StreamName,
  ): IngestTickUseCase => new IngestTickUseCase(bus, buffer, stream),
}

const bufferUseCaseProvider: Provider = {
  provide: BufferTickUseCase,
  inject: [TICK_BUFFER],
  useFactory: (buffer: InMemoryTickBuffer): BufferTickUseCase =>
    new BufferTickUseCase(buffer),
}

const flushProvider: Provider = {
  provide: FlushBufferUseCase,
  inject: [BUS, TICK_BUFFER, STREAM_NAME],
  useFactory: (
    bus: RedisProtocolBus,
    buffer: InMemoryTickBuffer,
    stream: StreamName,
  ): FlushBufferUseCase => new FlushBufferUseCase(bus, buffer, stream),
}

const healthMonitorUseCaseProvider: Provider = {
  provide: HealthMonitorUseCase,
  inject: [HEALTH_MONITOR, EXCHANGE_GATEWAY, FlushBufferUseCase],
  useFactory: (
    monitor: BusHealthMonitor,
    exchange: ExchangeGateway,
    flush: FlushBufferUseCase,
  ): HealthMonitorUseCase =>
    new HealthMonitorUseCase(monitor, exchange, flush, {
      cutoffMs: 10_000,
      onLog: log,
    }),
}

const candleStoreProvider: Provider = {
  provide: CANDLE_STORE,
  useFactory: (): InMemoryCandleStore => new InMemoryCandleStore(),
}

const candlePublisherProvider: Provider = {
  provide: CANDLE_PUBLISHER,
  useFactory: (): RedisCandlePublisher => {
    const url = process.env.ARGOS_BROKER_URL
    if (!url) throw new Error("AppModule: ARGOS_BROKER_URL is required")
    return new RedisCandlePublisher({
      url,
      streamPrefix: "candles:",
    })
  },
}

const buildCandlesProvider: Provider = {
  provide: BuildCandlesUseCase,
  inject: [CANDLE_STORE, CANDLE_PUBLISHER],
  useFactory: (
    store: InMemoryCandleStore,
    publisher: RedisCandlePublisher,
  ): BuildCandlesUseCase => new BuildCandlesUseCase(store, publisher),
}

const featureCalculatorProvider: Provider = {
  provide: FEATURE_CALCULATOR,
  useFactory: (): TechnicalIndicatorCalculator =>
    new TechnicalIndicatorCalculator(),
}

const featurePublisherProvider: Provider = {
  provide: FEATURE_PUBLISHER,
  useFactory: (): RedisFeaturePublisher => {
    const url = process.env.ARGOS_BROKER_URL
    if (!url) throw new Error("AppModule: ARGOS_BROKER_URL is required")
    return new RedisFeaturePublisher({ url, streamPrefix: "features:" })
  },
}

const calculateFeaturesProvider: Provider = {
  provide: CalculateFeaturesUseCase,
  inject: [FEATURE_CALCULATOR, FEATURE_PUBLISHER],
  useFactory: (
    calculator: TechnicalIndicatorCalculator,
    publisher: RedisFeaturePublisher,
  ): CalculateFeaturesUseCase =>
    new CalculateFeaturesUseCase(calculator, publisher),
}

const eventStoreProvider: Provider = {
  provide: EVENT_STORE,
  useFactory: (): FileEventStore => {
    const baseDir = process.env.ARGOS_HISTORICAL_DIR ?? "./data/historical"
    return new FileEventStore({ baseDir })
  },
}

const replayProvider: Provider = {
  provide: ReplayMarketUseCase,
  inject: [EVENT_STORE, BUS],
  useFactory: (
    store: EventStore,
    bus: RedisProtocolBus,
  ): ReplayMarketUseCase => new ReplayMarketUseCase(store, bus),
}

@Module({
  imports: [ConfigModule.forRoot({ isGlobal: true })],
  controllers: [HealthController, HealthControllerBus],
  providers: [
    streamNameProvider,
    busProvider,
    tickBufferProvider,
    exchangeProvider,
    healthMonitorProvider,
    ingestProvider,
    bufferUseCaseProvider,
    flushProvider,
    healthMonitorUseCaseProvider,
    candleStoreProvider,
    candlePublisherProvider,
    buildCandlesProvider,
    featureCalculatorProvider,
    featurePublisherProvider,
    calculateFeaturesProvider,
    eventStoreProvider,
    replayProvider,
    TickPipelineService,
    CandlePipelineService,
    FeaturePipelineService,
    HistoricalPipelineService,
    NotificationConsumer,
  ],
})
export class AppModule {}
