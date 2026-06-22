import { readFileSync, existsSync } from "fs"
import { join } from "path"

export type EnvironmentMode =
  | "BACKTESTING"
  | "PAPER_TRADING"
  | "LIVE"
  | "LIVE_SIMULATION"

const TESTNET_MODES: EnvironmentMode[] = ["PAPER_TRADING", "LIVE_SIMULATION"]

const WS_PROD = "wss://stream.binance.com:9443"
const WS_TESTNET = "wss://stream.binancefuture.com"

interface ConfigJson {
  exchanges?: Array<{ id: string; type?: string; testnet?: boolean }>
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
      } catch {
        /* skip invalid */
      }
    }
  }
  return null
}

function isTestnetFromConfig(config: ConfigJson | null): boolean {
  if (!config?.exchanges) return false
  return config.exchanges.some((ex) => ex.testnet === true)
}

function isTestnetFromEnv(): boolean {
  const mode = (process.env.ENVIRONMENT_MODE ?? "").toUpperCase() as EnvironmentMode
  return TESTNET_MODES.includes(mode)
}

export function isTestnet(): boolean {
  const config = readConfigJson()
  return isTestnetFromEnv() || isTestnetFromConfig(config)
}

export function resolveWsBaseUrl(): string {
  const explicitUrl = process.env.EXCHANGE_WS_URL
  if (explicitUrl?.trim()) return explicitUrl.trim()
  return isTestnet() ? WS_TESTNET : WS_PROD
}
