/**
 * Preflight Security Check — ARGOS data-engine (TypeScript).
 *
 * Lee `required_secrets.json` (schema compartido con Python) y valida
 * que las variables de entorno requeridas estén presentes según el modo.
 *
 * USO:
 *   import { preflightCheck } from "./infrastructure/security/preflight"
 *   preflightCheck()  // llama antes de NestFactory.create()
 *
 * Arquitectura:
 *   - Infisical (DEV/STAGING) / systemd (PROD) inyectan las vars.
 *   - Este módulo verifica que estén presentes antes de iniciar.
 *   - No depende de dotenv. Lee directo de process.env.
 */

import { readFileSync, existsSync } from "fs"
import { join, resolve } from "path"

interface SecretsSchema {
  production: string[]
  testnet: string[]
  notifications: string[]
  optional: string[]
  mode_requirements: Record<
    string,
    { required_groups: string[]; description: string }
  >
}

function loadSchema(): SecretsSchema {
  const searchPaths = [
    join(__dirname, "..", "..", "..", "..", "..", "security", "required_secrets.json"),
    join(process.cwd(), "security", "required_secrets.json"),
    resolve("/etc/argos/required_secrets.json"),
  ]

  for (const p of searchPaths) {
    if (existsSync(p)) {
      try {
        const raw = readFileSync(p, "utf8")
        return JSON.parse(raw) as SecretsSchema
      } catch {
        // try next path
      }
    }
  }

  // Fallback: schema embebido (último recurso)
  return {
    production: ["EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "ARGOS_BROKER_URL"],
    testnet: ["BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_SECRET"],
    notifications: ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    optional: [
      "EXCHANGE_PASSPHRASE",
      "EXCHANGE_ID",
      "EXCHANGE_WS_URL",
      "DISCORD_WEBHOOK_URL",
      "USE_PYTORCH",
      "RISK_PCT",
      "DRAWDOWN_PCT",
      "ARGOS_HISTORICAL_DIR",
    ],
    mode_requirements: {
      BACKTESTING: { required_groups: [], description: "No secrets required" },
      PAPER_TRADING: {
        required_groups: ["testnet", "notifications"],
        description: "Requires testnet + notifications",
      },
      LIVE: {
        required_groups: ["production", "notifications"],
        description: "Requires all secrets",
      },
      LIVE_SIMULATION: {
        required_groups: ["testnet", "notifications"],
        description: "Simulation mode — requires testnet + notifications",
      },
    },
  }
}

function getRequiredVars(schema: SecretsSchema, mode: string): string[] {
  const modeCfg = schema.mode_requirements[mode]
  if (!modeCfg) return []

  const required: string[] = []
  for (const groupName of modeCfg.required_groups) {
    const group = (schema as unknown as Record<string, string[]>)[groupName]
    if (group) {
      required.push(...group)
    }
  }
  return required
}

function checkVars(required: string[]): string[] {
  return required.filter((v) => {
    const val = process.env[v]
    return !val || val.trim() === ""
  })
}

export function preflightCheck(): void {
  const mode = (process.env.ENVIRONMENT_MODE ?? "BACKTESTING").toUpperCase()
  const schema = loadSchema()
  const required = getRequiredVars(schema, mode)
  const missing = checkVars(required)

  if (missing.length > 0) {
    console.error(
      `[security] PREFLIGHT FAILED [data-engine] mode=${mode}`,
    )
    console.error(`  Missing required secrets: ${missing.join(", ")}`)
    console.error(
      "  Injection: use 'infisical run' (DEV/STAGING) or set env vars (PROD)",
    )
    process.exit(1)
  }

  // eslint-disable-next-line no-console
  console.info(`[security] PREFLIGHT PASSED [data-engine] mode=${mode}`)
}
