export interface AggTradeData {
  symbol: string
  tradeId: number
  price: number
  quantity: number
  isBuyerMaker: boolean
  ts: number
}

export class AggTrade {
  private constructor(
    readonly symbol: string,
    readonly tradeId: number,
    readonly price: number,
    readonly quantity: number,
    readonly isBuyerMaker: boolean,
    readonly ts: number,
  ) {}

  static create(d: AggTradeData): AggTrade {
    if (!d.symbol || d.symbol.length < 2) throw new Error("AggTrade: symbol required")
    if (typeof d.price !== "number" || d.price <= 0) throw new Error("AggTrade: invalid price")
    if (typeof d.quantity !== "number" || d.quantity <= 0) throw new Error("AggTrade: invalid quantity")
    if (typeof d.ts !== "number" || d.ts <= 0) throw new Error("AggTrade: invalid ts")
    return new AggTrade(d.symbol, d.tradeId, d.price, d.quantity, d.isBuyerMaker, d.ts)
  }

  toJSON(): AggTradeData {
    return {
      symbol: this.symbol, tradeId: this.tradeId,
      price: this.price, quantity: this.quantity,
      isBuyerMaker: this.isBuyerMaker, ts: this.ts,
    }
  }

  static fromJSON(d: AggTradeData): AggTrade {
    return AggTrade.create(d)
  }
}
