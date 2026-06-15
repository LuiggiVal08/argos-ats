import { ExchangeGateway } from "../../application/ports/exchange-gateway.port"
import { BinanceWebSocketAdapter } from "./binance-websocket.adapter"
import { Symbol } from "../../domain/value-objects/symbol"

export type ExchangeType = "binance"

const EXCHANGE_TYPE_ENV = "EXCHANGE_TYPE"
const EXCHANGE_WS_URL_ENV = "EXCHANGE_WS_URL"
const SYMBOL_ENV = "SYMBOL"

const BINANCE_BASE = "wss://stream.binance.com:9443"
const BINANCE_COMBINED = `${BINANCE_BASE}/stream?streams=`

function binanceUrl(symbol: string): string {
  const s = Symbol.parse(symbol)
  const stream = `${s.toStreamId().toLowerCase()}@trade`
  return `${BINANCE_COMBINED}${stream}`
}

export function createExchangeAdapter(
  logger?: (msg: string) => void,
): ExchangeGateway {
  const type = (process.env[EXCHANGE_TYPE_ENV] ?? "binance").toLowerCase() as ExchangeType
  const symbol = process.env[SYMBOL_ENV] ?? "BTC/USDT"
  const explicitUrl = process.env[EXCHANGE_WS_URL_ENV]

  switch (type) {
    case "binance":
      return new BinanceWebSocketAdapter({
        url: explicitUrl ?? binanceUrl(symbol),
        logger,
      })
    default:
      throw new Error(
        `Unknown exchange type '${type}'. Supported: binance`,
      )
  }
}
