import { parseQuantity } from "../in-memory-tick-buffer"

describe("parseQuantity", () => {
  it("parses 1 BTC", () => {
    expect(parseQuantity("1.0")).toBe(100_000_000n)
  })

  it("parses 1.5 BTC", () => {
    expect(parseQuantity("1.5")).toBe(150_000_000n)
  })

  it("parses 0.00000001 BTC (1 satoshi)", () => {
    expect(parseQuantity("0.00000001")).toBe(1n)
  })

  it("parses whole integer quantity", () => {
    expect(parseQuantity("2")).toBe(200_000_000n)
  })

  it("truncates beyond 8 decimals", () => {
    expect(parseQuantity("0.123456789")).toBe(12_345_678n)
  })

  it("returns 0n for sub-satoshi quantity", () => {
    expect(parseQuantity("0.000000001")).toBe(0n)
  })

  it("parses large quantity without overflow", () => {
    expect(parseQuantity("99999.99999999")).toBe(9_999_999_999_999n)
  })

  it("parses zero", () => {
    expect(parseQuantity("0.0")).toBe(0n)
  })

  it("parses no decimal point", () => {
    expect(parseQuantity("42")).toBe(4_200_000_000n)
  })

  it("parses very small scientific-style string", () => {
    expect(parseQuantity("0.000000001234")).toBe(0n)
  })
})
