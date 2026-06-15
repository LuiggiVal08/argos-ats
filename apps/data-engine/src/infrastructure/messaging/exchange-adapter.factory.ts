import { readFileSync, existsSync } from "fs"
import { join } from "path"
import { ExchangeGateway } from "../../application/ports/exchange-gateway.port"
import { BinanceWebSocketAdapter } from "./binance-websocket.adapter"
import { Symbol } from "../../domain/value-objects/symbol"

export type ExchangeType = "binance"

const EXCHANGE_TYPE_ENV = "EXCHANGE_TYPE"
const EXCHANGE_WS_URL_ENV = "EXCHANGE_WS_URL"
const SYMBOL_ENV = "SYMBOL"

const BINANCE_BASE = "wss://stream.binance.com:9443"
const BINANCE_COMBINED = `${BINANCE_BASE}/stream?streams=`

function parseSymbols(raw: string): Symbol[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => Symbol.parse(s))
}

function binanceUrl(symbols: Symbol[]): string {
  const streams = symbols
    .map((s) => `${s.toStreamId().toLowerCase()}@trade`)
    .join("/")
  return `${BINANCE_COMBINED}${streams}`
}

interface ConfigJson {
  exchanges?: Array<{ id: string; type?: string; testnet?: boolean }>
  symbols?: string[]
}

function readConfigJson(): ConfigJson | null {
  const paths = [
    join(__dirname, "..", "..", "..", "..", "..", "config.json"),
    join(process.cwd(), "config.json"),
  ]
  for (const p of paths) {
    if (existsSync(p)) {
      try {
        return JSON.parse(readFileSync(p, "utf8")) as ConfigJson
      } catch { /* skip invalid */ }
    }
  }
  return null
}

function resolveSymbols(config: ConfigJson | null): Symbol[] {
  const envVal = process.env[SYMBOL_ENV]
  if (envVal) return parseSymbols(envVal)

  if (config?.symbols && config.symbols.length > 0) {
    return config.symbols.map((s) => Symbol.parse(s))
  }

  return [Symbol.parse("BTC/USDT")]
}

function resolveExchangeType(config: ConfigJson | null): ExchangeType {
  const envVal = process.env[EXCHANGE_TYPE_ENV]
  if (envVal) return envVal.toLowerCase() as ExchangeType

  if (config?.exchanges && config.exchanges.length > 0) {
    return config.exchanges[0].id as ExchangeType
  }

  return "binance"
}

export function createExchangeAdapter(
  logger?: (msg: string) => void,
): ExchangeGateway {
  const config = readConfigJson()
  const type = resolveExchangeType(config)
  const symbols = resolveSymbols(config)
  const explicitUrl = process.env[EXCHANGE_WS_URL_ENV]

  switch (type) {
    case "binance": {
      const url = explicitUrl ?? binanceUrl(symbols)
      return new BinanceWebSocketAdapter({ url, logger })
    }
    default:
      throw new Error(
        `Unknown exchange type '${type}'. Supported: binance`,
      )
  }
}

export { parseSymbols, resolveSymbols }
