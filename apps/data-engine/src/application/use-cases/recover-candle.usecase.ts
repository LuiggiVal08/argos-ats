import { Candle } from "../../domain/entities/candle"
import { Symbol } from "../../domain/value-objects/symbol"
import { Timeframe } from "../../domain/value-objects/timeframe"
import { HistoricalDataProvider } from "../ports/historical-data-provider.port"
import { CandleStore } from "../ports/candle-store.port"
import { CandlePublisher } from "../ports/candle-publisher.port"

export interface RecoverResult {
  recovered: Candle | null
  discarded: boolean
}

export class RecoverCandleUseCase {
  constructor(
    private readonly store: CandleStore,
    private readonly historicalProvider: HistoricalDataProvider,
    private readonly publisher: CandlePublisher,
  ) {}

  async execute(
    symbol: Symbol,
    timeframe: Timeframe,
    expectedOpenTs: number,
  ): Promise<RecoverResult> {
    const state = this.store.get(symbol, timeframe)
    if (!state) return { recovered: null, discarded: true }

    if (state.current.openTs !== expectedOpenTs) {
      return { recovered: null, discarded: true }
    }

    try {
      const candles = await this.historicalProvider.fetchCandles(
        symbol,
        timeframe,
        expectedOpenTs,
        expectedOpenTs + timeframe.toMilliseconds(),
      )

      if (candles.length > 0) {
        const recovered = candles[0].markComplete()
        this.store.set(symbol, timeframe, {
          current: recovered,
          previous: state.previous,
        })
        await this.publisher.publishCandle(recovered)
        return { recovered, discarded: false }
      }

      const incomplete = state.current.markIncomplete()
      this.store.set(symbol, timeframe, {
        current: incomplete,
        previous: state.previous,
      })
      await this.publisher.publishCandle(incomplete)
      return { recovered: incomplete, discarded: false }
    } catch {
      this.store.remove(symbol, timeframe)
      console.warn(`[recover] failed to recover candle ${symbol} ${timeframe}`)
      return { recovered: null, discarded: true }
    }
  }
}
