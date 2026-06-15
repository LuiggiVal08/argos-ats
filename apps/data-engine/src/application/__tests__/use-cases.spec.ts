import { IngestTickUseCase } from "../use-cases/ingest-tick.usecase"
import { BufferTickUseCase } from "../use-cases/buffer-tick.usecase"
import { FlushBufferUseCase } from "../use-cases/flush-buffer.usecase"
import { HealthMonitorUseCase } from "../use-cases/health-monitor.usecase"
import { MessageBus } from "../ports/message-bus.port"
import { ExchangeGateway, ExchangeConnectionState } from "../ports/exchange-gateway.port"
import { TickBuffer } from "../ports/tick-buffer.port"
import { HealthMonitor } from "../ports/health-monitor.port"
import { Tick } from "../../domain/entities/tick"
import { Price } from "../../domain/value-objects/price"
import { Symbol } from "../../domain/value-objects/symbol"
import { StreamName } from "../../domain/value-objects/stream-name"

const makeTick = (i: number): Tick =>
  Tick.create({
    symbol: Symbol.parse("BTC/USDT"),
    price: Price.parse("60000.00", 8),
    quantity: 1n,
    side: "buy",
    ts: 1700000000000 + i,
    tradeId: String(i),
  })

class FakeBus implements MessageBus {
  published: Tick[] = []
  failPublish = false
  healthy = true
  subscribers: Array<(t: Tick) => Promise<void>> = []
  async publish(_s: StreamName, t: Tick): Promise<void> {
    if (this.failPublish) throw new Error("broker-down")
    this.published.push(t)
  }
  async subscribe(
    _s: StreamName,
    h: (t: Tick) => Promise<void>,
  ): Promise<() => Promise<void>> {
    this.subscribers.push(h)
    return async () => {}
  }
  async ping(): Promise<boolean> {
    return this.healthy
  }
  async close(): Promise<void> {}
}

class FakeBuffer implements TickBuffer {
  items: Tick[] = []
  constructor(public readonly max: number = 100) {}
  async push(t: Tick): Promise<void> {
    this.items.push(t)
    while (this.items.length > this.max) this.items.shift()
  }
  size(): number {
    return this.items.length
  }
  capacity(): number {
    return this.max
  }
  async drain(): Promise<Tick[]> {
    return this.items.splice(0, this.items.length)
  }
}

class FakeGateway implements ExchangeGateway {
  stateValue: ExchangeConnectionState = "idle"
  lastTickAgeMs: number | null = null
  async start(_: (t: Tick) => Promise<void>): Promise<void> {
    this.stateValue = "open"
  }
  async close(): Promise<void> {
    this.stateValue = "closed"
  }
  state(): ExchangeConnectionState {
    return this.stateValue
  }
}

class FakeMonitor implements HealthMonitor {
  healthy = true
  start(): void {}
  async stop(): Promise<void> {}
  isHealthy(): boolean {
    return this.healthy
  }
}

describe("IngestTickUseCase", () => {
  it("publishes on success", async () => {
    const bus = new FakeBus()
    const buf = new FakeBuffer()
    const stream = StreamName.forTicks(Symbol.parse("BTC/USDT"), "ticks:")
    const uc = new IngestTickUseCase(bus, buf, stream)
    const r = await uc.execute(makeTick(1))
    expect(r.published).toBe(true)
    expect(r.buffered).toBe(false)
    expect(bus.published.length).toBe(1)
    expect(buf.size()).toBe(0)
  })

  it("buffers on broker failure", async () => {
    const bus = new FakeBus()
    bus.failPublish = true
    const buf = new FakeBuffer()
    const stream = StreamName.forTicks(Symbol.parse("BTC/USDT"), "ticks:")
    const uc = new IngestTickUseCase(bus, buf, stream)
    const r = await uc.execute(makeTick(1))
    expect(r.published).toBe(false)
    expect(r.buffered).toBe(true)
    expect(bus.published.length).toBe(0)
    expect(buf.size()).toBe(1)
  })
})

describe("BufferTickUseCase", () => {
  it("adds to buffer", async () => {
    const buf = new FakeBuffer()
    const uc = new BufferTickUseCase(buf)
    await uc.execute(makeTick(1))
    await uc.execute(makeTick(2))
    expect(buf.size()).toBe(2)
  })
})

describe("FlushBufferUseCase", () => {
  it("drains and publishes everything when broker is up", async () => {
    const bus = new FakeBus()
    const buf = new FakeBuffer()
    const stream = StreamName.forTicks(Symbol.parse("BTC/USDT"), "ticks:")
    const uc = new FlushBufferUseCase(bus, buf, stream)
    await buf.push(makeTick(1))
    await buf.push(makeTick(2))
    const r = await uc.execute()
    expect(r.drained).toBe(2)
    expect(r.published).toBe(2)
    expect(r.reBuffered).toBe(0)
    expect(bus.published.length).toBe(2)
  })

  it("stops flushing on first broker error (preserves remaining)", async () => {
    const bus = new FakeBus()
    const buf = new FakeBuffer()
    const stream = StreamName.forTicks(Symbol.parse("BTC/USDT"), "ticks:")
    await buf.push(makeTick(1))
    await buf.push(makeTick(2))
    await buf.push(makeTick(3))
    bus.failPublish = true
    const uc = new FlushBufferUseCase(bus, buf, stream)
    const r = await uc.execute()
    expect(r.drained).toBe(3)
    expect(r.published).toBe(0)
    // The failed tick and all subsequent ticks are re-buffered in
    // original order. No tick is lost.
    expect(r.reBuffered).toBe(3)
    expect(buf.size()).toBe(3)
  })
})

describe("HealthMonitorUseCase", () => {
  it("closes exchange on cutoff", async () => {
    jest.useFakeTimers()
    const monitor = new FakeMonitor()
    const exchange = new FakeGateway()
    const bus = new FakeBus()
    const buf = new FakeBuffer()
    const stream = StreamName.forTicks(Symbol.parse("BTC/USDT"), "ticks:")
    const flush = new FlushBufferUseCase(bus, buf, stream)
    const log: string[] = []
    const uc = new HealthMonitorUseCase(monitor, exchange, flush, {
      cutoffMs: 10_000,
      onLog: (m) => log.push(m),
    })

    monitor.healthy = false
    await uc.tick()
    expect(exchange.state()).toBe("idle")
    jest.advanceTimersByTime(10_001)
    // Allow microtasks (the setTimeout callback) to flush.
    await Promise.resolve()
    expect(exchange.state()).toBe("closed")
    expect(log.some((m) => m.includes("cutoff reached"))).toBe(true)
    jest.useRealTimers()
  })

  it("does not close if broker recovers before cutoff", async () => {
    jest.useFakeTimers()
    const monitor = new FakeMonitor()
    const exchange = new FakeGateway()
    const bus = new FakeBus()
    const buf = new FakeBuffer()
    const stream = StreamName.forTicks(Symbol.parse("BTC/USDT"), "ticks:")
    const flush = new FlushBufferUseCase(bus, buf, stream)
    const log: string[] = []
    const uc = new HealthMonitorUseCase(monitor, exchange, flush, {
      cutoffMs: 10_000,
      onLog: (m) => log.push(m),
    })

    monitor.healthy = false
    await uc.tick()
    jest.advanceTimersByTime(5_000)
    monitor.healthy = true
    await uc.tick()
    jest.advanceTimersByTime(10_000)
    expect(exchange.state()).toBe("idle")
    expect(log.some((m) => m.includes("recovered"))).toBe(true)
    jest.useRealTimers()
  })
})
