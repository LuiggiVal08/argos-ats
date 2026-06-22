/**
 * E2E-2: Execution loop (order → fill)
 *
 * Validates the DE-only execution contract:
 *   1. Publish an order to orders:execution
 *   2. Validate order payload against schema
 *   3. Simulate DE execution: publish fill to fills:execution
 *   4. Validate fill payload against schema
 *
 * Requires ARGOS_BROKER_URL env var. Skipped if unset.
 */

import { randomUUID } from "crypto"

const REDIS_URL = process.env.ARGOS_BROKER_URL
const describeWithRedis = REDIS_URL ? describe : describe.skip

import { validateOrder, validateFill } from "../contracts"

describeWithRedis("E2E-2: order → fill (execution loop, DE only)", () => {
  let Redis: typeof import("ioredis")
  let redis: import("ioredis").Redis

  const ORDER_STREAM = `orders:execution`
  const FILL_STREAM = `fills:execution`

  beforeAll(async () => {
    Redis = await import("ioredis")
    redis = new Redis.default(REDIS_URL!)
  })

  afterAll(async () => {
    await redis.quit()
  })

  it("DE receives valid order from orders:execution", async () => {
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

    const orderValidation = validateOrder(order as unknown as Record<string, unknown>)
    expect(orderValidation.ok).toBe(true)

    await redis.xadd(ORDER_STREAM, "*", "p", JSON.stringify(order))

    const read = await (redis as any).xread("BLOCK", 2000, "COUNT", 10, "STREAMS", ORDER_STREAM, "0")
    expect(read).not.toBeNull()
    if (Array.isArray(read)) {
      expect(read.length).toBeGreaterThanOrEqual(1)
    }
  })

  it("DE produces valid fill to fills:execution after execution", async () => {
    const fill = {
      fill_id: randomUUID(),
      order_id: randomUUID(),
      symbol: "BTC/USDT",
      side: "BUY" as const,
      filled_qty: "0.01",
      avg_price: "45100.00",
      status: "FILLED" as const,
      exchange_order_id: "binance-order-12345",
      timestamp: Date.now(),
      schema_version: 1,
    }

    const fillValidation = validateFill(fill as unknown as Record<string, unknown>)
    expect(fillValidation.ok).toBe(true)

    await redis.xadd(FILL_STREAM, "*", "p", JSON.stringify(fill))

    const read = await (redis as any).xread("BLOCK", 2000, "COUNT", 10, "STREAMS", FILL_STREAM, "0")
    expect(read).not.toBeNull()
    if (Array.isArray(read)) {
      expect(read.length).toBeGreaterThanOrEqual(1)
    }
  })

  it("rejects malformed fill payload", () => {
    expect(validateFill({} as unknown as Record<string, unknown>).ok).toBe(false)
  })
})
