/**
 * E2E-1: Contract flow (candle → signal → order)
 *
 * Validates the full event chain across Redis without any service running:
 *   1. Publish a candle to market:candles:1h  (DE role)
 *   2. Validate candle payload against schema
 *   3. Simulate AE: read candle, publish signal to signals:trading
 *   4. Validate signal payload against schema
 *   5. Simulate AE: publish order to orders:execution
 *   6. Validate order payload against schema
 *
 * Requires ARGOS_BROKER_URL env var. Skipped if unset.
 */

import { randomUUID } from "crypto"

const REDIS_URL = process.env.ARGOS_BROKER_URL
const describeWithRedis = REDIS_URL ? describe : describe.skip

import { validateCandle, validateSignal, validateOrder } from "../contracts"

describeWithRedis("E2E-1: candle → signal → order (contract flow)", () => {
  let Redis: typeof import("ioredis")
  let redis: import("ioredis").Redis
  const ns = randomUUID().slice(0, 8)

  const CANDLE_STREAM = `market:candles:1h`
  const SIGNAL_STREAM = `signals:trading`
  const ORDER_STREAM = `orders:execution`

  beforeAll(async () => {
    Redis = await import("ioredis")
    redis = new Redis.default(REDIS_URL!)
  })

  afterAll(async () => {
    await redis.del(`${CANDLE_STREAM}:${ns}`)
    await redis.del(`${SIGNAL_STREAM}:${ns}`)
    await redis.del(`${ORDER_STREAM}:${ns}`)
    await redis.quit()
  })

  it("DE publishes valid candle to market:candles:1h", async () => {
    const candle = {
      symbol: "BTC/USDT",
      timeframe: "1h",
      open: "45000.50",
      high: "45200.00",
      low: "44900.00",
      close: "45100.00",
      volume: "1234.567",
      timestamp: Date.now(),
      is_complete: true,
      schema_version: 1,
    }

    const validation = validateCandle(candle as unknown as Record<string, unknown>)
    expect(validation.ok).toBe(true)

    await redis.xadd(CANDLE_STREAM, "*", "p", JSON.stringify(candle))

    const read = await (redis as any).xread("BLOCK", 2000, "COUNT", 1, "STREAMS", CANDLE_STREAM, "0")
    expect(read).not.toBeNull()
    expect(Array.isArray(read)).toBe(true)
    if (Array.isArray(read)) {
      expect(read.length).toBeGreaterThanOrEqual(1)
    }
  })

  it("AE publishes valid signal to signals:trading", async () => {
    const signal = {
      signal_id: randomUUID(),
      symbol: "BTC/USDT",
      action: "BUY" as const,
      confidence: 0.73,
      model_version: "novaquant-v1.2.3",
      regime: "TRENDING" as const,
      timestamp: Date.now(),
      schema_version: 1,
    }

    const validation = validateSignal(signal as unknown as Record<string, unknown>)
    expect(validation.ok).toBe(true)

    await redis.xadd(SIGNAL_STREAM, "*", "p", JSON.stringify(signal))

    const read = await (redis as any).xread("BLOCK", 2000, "COUNT", 1, "STREAMS", SIGNAL_STREAM, "0")
    expect(read).not.toBeNull()
    if (Array.isArray(read)) {
      expect(read.length).toBeGreaterThanOrEqual(1)
    }
  })

  it("AE publishes valid order to orders:execution", async () => {
    const order = {
      order_id: randomUUID(),
      signal_id: randomUUID(),
      symbol: "BTC/USDT",
      side: "BUY" as const,
      order_type: "MARKET" as const,
      amount: "0.01",
      idempotency_key: `exec:${randomUUID()}`,
      timestamp: Date.now(),
      schema_version: 1,
    }

    const validation = validateOrder(order as unknown as Record<string, unknown>)
    expect(validation.ok).toBe(true)

    await redis.xadd(ORDER_STREAM, "*", "p", JSON.stringify(order))

    const read = await (redis as any).xread("BLOCK", 2000, "COUNT", 1, "STREAMS", ORDER_STREAM, "0")
    expect(read).not.toBeNull()
    if (Array.isArray(read)) {
      expect(read.length).toBeGreaterThanOrEqual(1)
    }
  })

  it("rejects malformed payloads", () => {
    expect(validateCandle({} as unknown as Record<string, unknown>).ok).toBe(false)
    expect(validateSignal({} as unknown as Record<string, unknown>).ok).toBe(false)
    expect(validateOrder({} as unknown as Record<string, unknown>).ok).toBe(false)
  })
})
