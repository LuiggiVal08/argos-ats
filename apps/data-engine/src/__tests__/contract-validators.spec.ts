/**
 * Unit tests for contract validators — pure logic, no Redis.
 */

import { randomUUID } from "crypto"
import {
  validateCandle,
  validateSignal,
  validateOrder,
  validateFill,
  validateHeartbeat,
  validateError,
  ValidationResult,
} from "../contracts"

function validCandle(): Record<string, unknown> {
  return {
    symbol: "BTC/USDT",
    timeframe: "1h",
    open: "45000.50",
    high: "45200.00",
    low: "44900.00",
    close: "45100.00",
    volume: "1234.567",
    timestamp: 1718000000000,
    is_complete: true,
    schema_version: 1,
  }
}

function validSignal(): Record<string, unknown> {
  return {
    signal_id: randomUUID(),
    symbol: "BTC/USDT",
    action: "BUY",
    confidence: 0.73,
    model_version: "novaquant-v1.2.3",
    regime: "TRENDING",
    timestamp: 1718000000000,
    schema_version: 1,
  }
}

function validOrder(): Record<string, unknown> {
  const sigId = randomUUID()
  return {
    order_id: randomUUID(),
    signal_id: sigId,
    symbol: "BTC/USDT",
    side: "BUY",
    order_type: "MARKET",
    amount: "0.01",
    idempotency_key: `exec:${sigId}`,
    timestamp: 1718000000000,
    schema_version: 1,
  }
}

function validFill(): Record<string, unknown> {
  return {
    fill_id: randomUUID(),
    order_id: randomUUID(),
    symbol: "BTC/USDT",
    side: "BUY",
    filled_qty: "0.01",
    avg_price: "45100.00",
    status: "FILLED",
    exchange_order_id: "binance-order-12345",
    timestamp: 1718000000001,
    schema_version: 1,
  }
}

function expectErrors(r: ValidationResult, field?: string): void {
  expect(r.ok).toBe(false)
  if (r.ok) throw new Error("expected errors but got ok")
  expect(r.errors.length).toBeGreaterThan(0)
  if (field) {
    expect(r.errors.some(e => e.field === field)).toBe(true)
  }
}

// ── Candle ────────────────────────────────────────────────────────

describe("CandleValidator", () => {
  it("passes valid candle", () => {
    expect(validateCandle(validCandle()).ok).toBe(true)
  })

  it("rejects empty payload", () => {
    expectErrors(validateCandle({}))
  })

  it("rejects wrong timestamp type", () => {
    const p = validCandle()
    p.timestamp = "not_a_number"
    expectErrors(validateCandle(p), "timestamp")
  })

  it("rejects bad symbol pattern", () => {
    const p = validCandle()
    p.symbol = "btc/usdt"
    expectErrors(validateCandle(p), "symbol")
  })

  it("rejects bad timeframe enum", () => {
    const p = validCandle()
    p.timeframe = "2h"
    expectErrors(validateCandle(p), "timeframe")
  })
})

// ── Signal ────────────────────────────────────────────────────────

describe("SignalValidator", () => {
  it("passes valid signal", () => {
    expect(validateSignal(validSignal()).ok).toBe(true)
  })

  it("rejects missing signal_id", () => {
    const p = validSignal()
    delete p.signal_id
    expectErrors(validateSignal(p), "signal_id")
  })

  it("rejects confidence out of range", () => {
    const p = validSignal()
    p.confidence = 1.5
    expectErrors(validateSignal(p), "confidence")
    p.confidence = -0.1
    expectErrors(validateSignal(p), "confidence")
  })

  it("rejects bad action enum", () => {
    const p = validSignal()
    p.action = "HOLD"
    expectErrors(validateSignal(p), "action")
  })

  it("rejects bad regime enum", () => {
    const p = validSignal()
    p.regime = "UNKNOWN_REGIME"
    expectErrors(validateSignal(p), "regime")
  })
})

// ── Order ─────────────────────────────────────────────────────────

describe("OrderValidator", () => {
  it("passes valid order", () => {
    expect(validateOrder(validOrder()).ok).toBe(true)
  })

  it("rejects bad idempotency_key prefix", () => {
    const p = validOrder()
    p.idempotency_key = "wrong:prefix"
    expectErrors(validateOrder(p), "idempotency_key")
  })

  it("rejects bad side", () => {
    const p = validOrder()
    p.side = "HOLD"
    expectErrors(validateOrder(p), "side")
  })

  it("rejects non-MARKET order_type", () => {
    const p = validOrder()
    p.order_type = "LIMIT"
    expectErrors(validateOrder(p), "order_type")
  })
})

// ── Fill ──────────────────────────────────────────────────────────

describe("FillValidator", () => {
  it("passes valid fill", () => {
    expect(validateFill(validFill()).ok).toBe(true)
  })

  it("rejects empty payload", () => {
    expectErrors(validateFill({}))
  })

  it("rejects bad status enum", () => {
    const p = validFill()
    p.status = "PENDING"
    expectErrors(validateFill(p), "status")
  })
})

// ── Edge cases ────────────────────────────────────────────────────

describe("Contract validators (edge cases)", () => {
  it("all reject empty payload", () => {
    for (const fn of [validateCandle, validateSignal, validateOrder, validateFill, validateHeartbeat, validateError]) {
      expectErrors(fn({}))
    }
  })

  it("reject wrong schema_version", () => {
    for (const [fn, mk] of [
      [validateCandle, validCandle],
      [validateSignal, validSignal],
      [validateOrder, validOrder],
      [validateFill, validFill],
    ] as const) {
      const p = mk()
      p.schema_version = 2
      expectErrors(fn(p), "schema_version")
    }
  })

  it("extra fields are ignored", () => {
    const p = validCandle()
    p.extra_field = "ignored"
    expect(validateCandle(p).ok).toBe(true)
  })
})
