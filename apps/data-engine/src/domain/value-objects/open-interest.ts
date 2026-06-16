export interface OpenInterestData {
  symbol: string
  openInterest: number
  ts: number
}

export class OpenInterest {
  private constructor(
    readonly symbol: string,
    readonly openInterest: number,
    readonly ts: number,
  ) {}

  static create(d: OpenInterestData): OpenInterest {
    if (!d.symbol || d.symbol.length < 2) throw new Error("OpenInterest: symbol required")
    if (typeof d.openInterest !== "number" || d.openInterest < 0)
      throw new Error("OpenInterest: invalid openInterest")
    if (typeof d.ts !== "number" || d.ts <= 0) throw new Error("OpenInterest: invalid ts")
    return new OpenInterest(d.symbol, d.openInterest, d.ts)
  }

  toJSON(): OpenInterestData {
    return { symbol: this.symbol, openInterest: this.openInterest, ts: this.ts }
  }

  static fromJSON(d: OpenInterestData): OpenInterest {
    return OpenInterest.create(d)
  }
}
