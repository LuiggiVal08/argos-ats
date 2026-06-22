import { Logger } from "@nestjs/common"
import { Candle } from "../../domain/entities/candle"
import { FeatureVector } from "../../domain/entities/feature-vector"
import { FeatureCalculator } from "../../application/ports/feature-calculator.port"

const log = (m: string): void => Logger.log(m, "TechnicalIndicatorCalculator")

export class TechnicalIndicatorCalculator implements FeatureCalculator {
  execute(candles: Candle[]): FeatureVector {
    const last = candles[candles.length - 1]
    let vector = FeatureVector.create(last.symbol, last.timeframe, last.closeTs)

    const closes = candles.map((c) => c.close.toNumber())
    const highs = candles.map((c) => c.high.toNumber())
    const lows = candles.map((c) => c.low.toNumber())
    const volumes = candles.map((c) => c.volume.toNumber())

    try {
      vector = vector.add("pct_change", this.pctChange(closes))
    } catch (e) { log(`pct_change failed: ${(e as Error).message}`) }

    try {
      vector = vector.add("vol_sma_20", this.sma(volumes, 20))
    } catch (e) { log(`vol_sma_20 failed: ${(e as Error).message}`) }

    try {
      vector = vector.add("obv", this.obv(closes, volumes))
    } catch (e) { log(`obv failed: ${(e as Error).message}`) }

    if (closes.length >= 9) {
      try { vector = vector.add("ema_9", this.ema(closes, 9)) } catch (e) { log(`ema_9 failed: ${(e as Error).message}`) }
    }
    if (closes.length >= 21) {
      try { vector = vector.add("ema_21", this.ema(closes, 21)) } catch (e) { log(`ema_21 failed: ${(e as Error).message}`) }
    }
    if (closes.length >= 50) {
      try { vector = vector.add("ema_50", this.ema(closes, 50)) } catch (e) { log(`ema_50 failed: ${(e as Error).message}`) }
    }

    if (closes.length >= 26) {
      try {
        const m = this.macd(closes)
        vector = vector.add("macd", m.macd)
        vector = vector.add("macd_signal", m.signal)
        vector = vector.add("macd_histogram", m.histogram)
      } catch (e) { log(`macd failed: ${(e as Error).message}`) }
    }

    if (closes.length >= 14) {
      try { vector = vector.add("rsi_14", this.rsi(closes, 14)) } catch (e) { log(`rsi_14 failed: ${(e as Error).message}`) }
    }
    if (highs.length >= 14) {
      try { vector = vector.add("atr_14", this.atr(highs, lows, closes, 14)) } catch (e) { log(`atr_14 failed: ${(e as Error).message}`) }
    }
    if (highs.length >= 28) {
      try { vector = vector.add("adx_14", this.adx(highs, lows, closes, 14)) } catch (e) { log(`adx_14 failed: ${(e as Error).message}`) }
    }
    if (closes.length >= 20) {
      try { vector = vector.add("bbw_20", this.bbw(closes, 20)) } catch (e) { log(`bbw_20 failed: ${(e as Error).message}`) }
    }

    return vector
  }

  private pctChange(closes: number[]): number {
    if (closes.length < 2) return 0
    const prev = closes[closes.length - 2]
    if (prev === 0) return 0
    return ((closes[closes.length - 1] - prev) / prev) * 100
  }

  private smo(period: number): number {
    return 2 / (period + 1)
  }

  private ema(values: number[], period: number): number {
    if (values.length < period) throw new Error("not enough data")
    const k = this.smo(period)
    let ema = values.slice(0, period).reduce((a, b) => a + b, 0) / period
    for (let i = period; i < values.length; i++) {
      ema = values[i] * k + ema * (1 - k)
    }
    return ema
  }

  private sma(values: number[], period: number): number {
    if (values.length < period) throw new Error("not enough data")
    const slice = values.slice(values.length - period)
    return slice.reduce((a, b) => a + b, 0) / period
  }

  private macd(closes: number[]): { macd: number; signal: number; histogram: number } {
    if (closes.length < 35) throw new Error("not enough data for macd")
    const macdLine = this.ema(closes, 12) - this.ema(closes, 26)
    const macdValues = this.computeMacdLine(closes)
    const signal = this.ema(macdValues, 9)
    const histogram = macdLine - signal
    return { macd: macdLine, signal, histogram }
  }

  private computeMacdLine(closes: number[]): number[] {
    const k12 = this.smo(12)
    const k26 = this.smo(26)
    const result: number[] = []
    let ema12 = closes.slice(0, 12).reduce((a, b) => a + b, 0) / 12
    let ema26 = closes.slice(0, 26).reduce((a, b) => a + b, 0) / 26
    for (let i = 26; i < closes.length; i++) {
      ema12 = closes[i] * k12 + ema12 * (1 - k12)
      ema26 = closes[i] * k26 + ema26 * (1 - k26)
      result.push(ema12 - ema26)
    }
    return result
  }

  private rsi(closes: number[], period: number): number {
    if (closes.length < period + 1) throw new Error("not enough data")
    const changes: number[] = []
    for (let i = 1; i < closes.length; i++) {
      changes.push(closes[i] - closes[i - 1])
    }
    let avgGain = 0
    let avgLoss = 0
    for (let i = 0; i < period; i++) {
      if (changes[i] > 0) avgGain += changes[i]
      else avgLoss += Math.abs(changes[i])
    }
    avgGain /= period
    avgLoss /= period
    for (let i = period; i < changes.length; i++) {
      const gain = changes[i] > 0 ? changes[i] : 0
      const loss = changes[i] < 0 ? Math.abs(changes[i]) : 0
      avgGain = (avgGain * (period - 1) + gain) / period
      avgLoss = (avgLoss * (period - 1) + loss) / period
    }
    if (avgLoss === 0) return avgGain === 0 ? 50 : 100
    if (avgGain === 0) return 0
    const rs = avgGain / avgLoss
    return 100 - 100 / (1 + rs)
  }

  private atr(highs: number[], lows: number[], closes: number[], period: number): number {
    if (highs.length < period + 1) throw new Error("not enough data")
    const trs: number[] = []
    for (let i = 1; i < highs.length; i++) {
      const hl = highs[i] - lows[i]
      const hc = Math.abs(highs[i] - closes[i - 1])
      const lc = Math.abs(lows[i] - closes[i - 1])
      trs.push(Math.max(hl, hc, lc))
    }
    let atr = trs.slice(0, period).reduce((a, b) => a + b, 0) / period
    for (let i = period; i < trs.length; i++) {
      atr = (atr * (period - 1) + trs[i]) / period
    }
    return atr
  }

  private obv(closes: number[], volumes: number[]): number {
    if (closes.length < 2) return 0
    let obv = 0
    for (let i = 1; i < closes.length; i++) {
      if (closes[i] > closes[i - 1]) obv += volumes[i]
      else if (closes[i] < closes[i - 1]) obv -= volumes[i]
    }
    return obv
  }

  private bbw(closes: number[], period: number): number {
    if (closes.length < period) throw new Error("not enough data")
    const slice = closes.slice(closes.length - period)
    const mean = slice.reduce((a, b) => a + b, 0) / period
    const variance = slice.reduce((sum, v) => sum + (v - mean) ** 2, 0) / period
    const stddev = Math.sqrt(variance)
    if (mean === 0) return 0
    return ((mean + 2 * stddev) - (mean - 2 * stddev)) / mean
  }

  private adx(highs: number[], lows: number[], closes: number[], period: number): number {
    if (highs.length < period * 2) throw new Error("not enough data")
    const trs: number[] = []
    const plusDms: number[] = []
    const minusDms: number[] = []
    for (let i = 1; i < highs.length; i++) {
      const hl = highs[i] - lows[i]
      const hc = Math.abs(highs[i] - closes[i - 1])
      const lc = Math.abs(lows[i] - closes[i - 1])
      trs.push(Math.max(hl, hc, lc))
      const upMove = highs[i] - highs[i - 1]
      const downMove = lows[i - 1] - lows[i]
      plusDms.push(upMove > downMove && upMove > 0 ? upMove : 0)
      minusDms.push(downMove > upMove && downMove > 0 ? downMove : 0)
    }
    let smoothedTr = trs.slice(0, period).reduce((a, b) => a + b, 0) / period
    let smoothedPlus = plusDms.slice(0, period).reduce((a, b) => a + b, 0) / period
    let smoothedMinus = minusDms.slice(0, period).reduce((a, b) => a + b, 0) / period
    const dxValues: number[] = []
    for (let i = period; i < trs.length; i++) {
      smoothedTr = (smoothedTr * (period - 1) + trs[i]) / period
      smoothedPlus = (smoothedPlus * (period - 1) + plusDms[i]) / period
      smoothedMinus = (smoothedMinus * (period - 1) + minusDms[i]) / period
      const plusDi = smoothedTr === 0 ? 0 : 100 * smoothedPlus / smoothedTr
      const minusDi = smoothedTr === 0 ? 0 : 100 * smoothedMinus / smoothedTr
      const diSum = plusDi + minusDi
      if (diSum === 0) {
        dxValues.push(0)
      } else {
        dxValues.push(100 * Math.abs(plusDi - minusDi) / diSum)
      }
    }
    if (dxValues.length < period) return dxValues[dxValues.length - 1] ?? 0
    let adx = dxValues.slice(0, period).reduce((a, b) => a + b, 0) / period
    for (let i = period; i < dxValues.length; i++) {
      adx = (adx * (period - 1) + dxValues[i]) / period
    }
    return adx
  }
}
