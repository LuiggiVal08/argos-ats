import Redis from "ioredis"
import { Candle } from "../../domain/entities/candle"
import { CandlePublisher } from "../../application/ports/candle-publisher.port"
import { validateCandle } from "../../contracts"
import { buildErrorPayload, emitError, formatValidationErrors } from "./emit-error"

export interface RedisCandlePublisherOptions {
  url: string
  streamPrefix: string
  connectTimeoutMs?: number
}

export class RedisCandlePublisher implements CandlePublisher {
  private readonly client: Redis

  constructor(opts: RedisCandlePublisherOptions) {
    this.client = new Redis(opts.url, {
      connectTimeout: opts.connectTimeoutMs ?? 2000,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    })
  }

  async publishCandle(candle: Candle): Promise<void> {
    const payload = this.candleToContract(candle)

    const result = validateCandle(payload)
    if (!result.ok) {
      await emitError(
        this.client,
        buildErrorPayload(
          "CANDLE_VALIDATION_FAILED",
          `Invalid candle payload: ${formatValidationErrors(result.errors)}`,
          "ERROR",
          { symbol: candle.symbol.value, timeframe: candle.timeframe.toString() },
        ),
      )
      return
    }

    const stream = `candles:${candle.symbol.toStreamId().toLowerCase()}:${candle.timeframe}`
    await this.client.xadd(stream, "*", "p", JSON.stringify(payload))
  }

  async close(): Promise<void> {
    try {
      await this.client.quit()
    } catch {
      console.warn("[candle-publisher] quit failed")
      this.client.disconnect()
    }
  }

  private candleToContract(candle: Candle): Record<string, unknown> {
    const json = candle.toJSON()
    return {
      symbol: json.symbol,
      timeframe: json.timeframe,
      open: json.open.minor,
      high: json.high.minor,
      low: json.low.minor,
      close: json.close.minor,
      volume: json.volume.minor,
      timestamp: json.closeTs,
      is_complete: json.isComplete,
      schema_version: 1,
    }
  }
}
