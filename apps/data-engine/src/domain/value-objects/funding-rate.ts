export interface FundingRateData {
  symbol: string
  fundingRate: number
  markPrice: number
  indexPrice: number
  settlePrice: number
  nextFundingTime: number
  ts: number
}

export class FundingRate {
  private constructor(
    readonly symbol: string,
    readonly fundingRate: number,
    readonly markPrice: number,
    readonly indexPrice: number,
    readonly settlePrice: number,
    readonly nextFundingTime: number,
    readonly ts: number,
  ) {}

  static create(d: FundingRateData): FundingRate {
    if (!d.symbol || d.symbol.length < 2) throw new Error("FundingRate: symbol required")
    if (typeof d.fundingRate !== "number" || !Number.isFinite(d.fundingRate))
      throw new Error("FundingRate: invalid fundingRate")
    if (typeof d.markPrice !== "number" || !Number.isFinite(d.markPrice))
      throw new Error("FundingRate: invalid markPrice")
    if (typeof d.ts !== "number" || d.ts <= 0) throw new Error("FundingRate: invalid ts")
    return new FundingRate(
      d.symbol, d.fundingRate, d.markPrice,
      d.indexPrice, d.settlePrice, d.nextFundingTime, d.ts,
    )
  }

  toJSON(): FundingRateData {
    return {
      symbol: this.symbol,
      fundingRate: this.fundingRate,
      markPrice: this.markPrice,
      indexPrice: this.indexPrice,
      settlePrice: this.settlePrice,
      nextFundingTime: this.nextFundingTime,
      ts: this.ts,
    }
  }

  static fromJSON(d: FundingRateData): FundingRate {
    return FundingRate.create(d)
  }
}
