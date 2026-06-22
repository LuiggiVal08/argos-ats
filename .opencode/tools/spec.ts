import { tool } from "@opencode-ai/plugin"
import { existsSync, readFileSync } from "fs"
import * as path from "path"

const ROOT = process.cwd()
const SPEC = path.join(ROOT, "spec.md")

const readSpec = (): string => {
  if (!existsSync(SPEC))
    throw new Error(
      "spec.md not found at project root. The argos-ats workflow assumes spec.md is the source of truth.",
    )
  return readFileSync(SPEC, "utf8")
}

const slug = (s: string) => s.replace(/[.#]/g, "").trim()

export const spec_summary = tool({
  description:
    "Return a compact digest of spec.md (one line per top-level section, e.g. '1.2 Stack Tecnologico'). Optionally filtered to specific section numbers.",
  args: {
    sections: tool.schema
      .array(tool.schema.string())
      .describe(
        "Optional list of section numbers to include, e.g. ['1.2', '5']. Empty = all.",
      )
      .default([]),
  },
  async execute({ sections = [] }: { sections?: string[] }) {
    const text = readSpec()
    const lines = text.split("\n")
    const headings: { level: number; num: string; title: string; line: number }[] = []
    const re = /^(#{1,6})\s+(\d+(?:\.\d+)*)?\.?\s*(.+?)\s*$/
    for (let i = 0; i < lines.length; i++) {
      const m = lines[i].match(re)
      if (m) {
        headings.push({
          level: m[1].length,
          num: m[2] ?? "",
          title: m[3],
          line: i + 1,
        })
      }
    }
    const wanted = new Set(sections.map(slug))
    const out: string[] = []
    for (const h of headings) {
      if (h.level !== 2) continue
      if (wanted.size > 0 && !wanted.has(slug(h.num))) continue
      out.push(`${h.num} ${h.title} (line ${h.line})`)
    }
    return {
      title: "spec_summary",
      output: out.length ? out.join("\n") : "No matching top-level sections found.",
    }
  },
})

export const spec_story = tool({
  description:
    "Extract a specific user story from spec.md section 5 by number (e.g. 2 -> Historia 2: Calculo Automatizado del Tamano de la Posicion).",
  args: {
    number: tool.schema.number().describe("Story number, e.g. 2"),
  },
  async execute({ number }) {
    const text = readSpec()
    const re = new RegExp(
      `####\\s+\\*?\\*?Historia de Usuario ${number}\\b[\\s\\S]*?(?=\\n####\\s+|\\n###\\s+|\\n##\\s+|\\Z)`,
      "m",
    )
    const m = text.match(re)
    if (!m) {
      const available = [
        ...text.matchAll(/####\s+\*?\*?Historia de Usuario (\d+)/g),
      ]
        .map((mm) => mm[1])
        .join(", ")
      return {
        title: `spec_story ${number}`,
        output: `Story ${number} not found. Available: ${available || "(none)"}`,
      }
    }
    return { title: `spec_story ${number}`, output: m[0].trim() }
  },
})

export const spec_invariants = tool({
  description:
    "Return the hard invariants extracted from spec.md: risk caps, drawdown thresholds, environment modes, hexagonal rules, OWASP secrets policy. Useful to inject as a checklist before code changes.",
  args: {},
  async execute() {
    const invariants: string[] = [
      "RISK: Loss per trade MUST NOT exceed 1% of free balance (spec section 5 Historia 2).",
      "SL DISTANCE: Stop-loss distance MUST be derived from ATR, not a fixed percentage.",
      "DRAWDOWN: Daily drawdown >= 5% MUST trip the Circuit Breaker (spec section 5 Historia 3).",
      "CIRCUIT BREAKER actions: cancel all open orders, close positions at market, set ENVIRONMENT_MODE=PASIVO, log the lockout, halt the trading loop.",
      "ENVIRONMENT_MODE MUST be one of BACKTESTING | PAPER_TRADING | LIVE.",
      "LIVE mode MUST abort init (exit code 1) if any required secret env var is missing or empty (spec section 5 Historia 5 sad path).",
      "SECRETS: API keys, private keys, and signatures MUST come from env vars injected at runtime. Hardcoded secrets are forbidden (spec section 4).",
      "HEXAGONAL: Domain MUST NOT import from Application or Infrastructure layers.",
      "HEXAGONAL: Application MUST NOT import concrete adapters — only port interfaces.",
      "HEXAGONAL: data-engine MUST NOT import from analytics-engine (and vice versa); shared contracts cross the message broker.",
      "STACK LEAKAGE: ioredis/ws/ccxt only inside apps/data-engine/src/infrastructure/. pandas/ta/tensorflow/ccxt only inside apps/analytics-engine/app/infrastructure/.",
      "TICK PIPELINE: Redis injection target < 2ms (spec section 5 Historia 1).",
      "RETRY: Order placement retries: max 3 in 500ms window; on total failure, send market order to liquidate.",
    ]
    return {
      title: "spec_invariants",
      output: invariants.map((s, i) => `${i + 1}. ${s}`).join("\n"),
    }
  },
})
