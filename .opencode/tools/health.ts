import { tool } from "@opencode-ai/plugin"
import { execFileSync } from "child_process"
import { existsSync } from "fs"
import * as path from "path"

const ROOT = process.cwd()

const tryRun = (cmd: string, args: string[]): string => {
  try {
    return execFileSync(cmd, args, {
      encoding: "utf8",
      cwd: ROOT,
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 5000,
    }).trim()
  } catch (e: any) {
    return `[error: ${(e.stderr ?? e.message ?? "").toString().trim() || "command failed"}]`
  }
}

const commandExists = (cmd: string): boolean => {
  try {
    execFileSync("command", ["-v", cmd], { stdio: "ignore" })
    return true
  } catch {
    return false
  }
}

const httpGet = async (
  url: string,
  timeoutMs = 2000
): Promise<{ ok: boolean; status: number; body: string }> => {
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), timeoutMs)
    const r = await fetch(url, { signal: ctrl.signal })
    clearTimeout(t)
    const body = await r.text()
    return { ok: r.ok, status: r.status, body: body.slice(0, 200) }
  } catch (e: any) {
    return { ok: false, status: 0, body: e?.message ?? "unreachable" }
  }
}

const expandConfigUrl = (raw: string | undefined): string => {
  if (!raw) return ""
  return raw.replace(/\$\{([A-Z_][A-Z0-9_]*)\}/g, (_, k) => process.env[k] ?? "")
}

const fileMarkers: { label: string; rel: string }[] = [
  { label: "spec.md", rel: "spec.md" },
  { label: "config.json", rel: "config.json" },
  { label: ".env.example (data-engine)", rel: "apps/data-engine/.env.example" },
  { label: ".env.example (analytics-engine)", rel: "apps/analytics-engine/.env.example" },
  { label: "package.json (data-engine)", rel: "apps/data-engine/package.json" },
  { label: "pyproject.toml (analytics-engine)", rel: "apps/analytics-engine/pyproject.json" },
  { label: "docker-compose.yml (optional)", rel: "docker-compose.yml" },
  { label: ".gitignore", rel: ".gitignore" },
  { label: ".gitattributes", rel: ".gitattributes" },
]

export const health_check = tool({
  description:
    "One-shot diagnostic. Auto-detects deployment model (Docker / bare metal). Reports git status, broker reachability, ENVIRONMENT_MODE, base files, and either docker compose ps or HTTP health endpoints. Markdown-formatted summary.",
  args: {},
  async execute() {
    const out: string[] = []
    out.push("# Argos-ATS health check")
    out.push("")

    out.push("## Git")
    out.push("```")
    out.push(tryRun("git", ["status", "--short"]) || "(clean working tree)")
    out.push("```")
    out.push("")

    out.push("## ENVIRONMENT_MODE")
    out.push("```")
    out.push(process.env.ENVIRONMENT_MODE ?? "(unset, default PAPER_TRADING)")
    out.push("```")
    out.push("")

    const dockerAvailable = commandExists("docker")
    const composeFile = path.join(ROOT, "docker-compose.yml")

    out.push("## Deployment model detection")
    if (dockerAvailable && existsSync(composeFile)) {
      out.push("- [docker] docker CLI found AND `docker-compose.yml` present — using Docker path")
    } else if (dockerAvailable) {
      out.push("- [bare-metal] docker CLI found BUT no `docker-compose.yml` — using bare-metal path")
    } else {
      out.push("- [bare-metal] docker CLI NOT found — using bare-metal path")
    }
    out.push("")

    if (dockerAvailable && existsSync(composeFile)) {
      out.push("## Docker compose services")
      out.push("```")
      out.push(tryRun("docker", ["compose", "ps"]))
      out.push("```")
      out.push("")
    } else {
      out.push("## HTTP health endpoints (bare metal)")
      const data = await httpGet("http://localhost:3000/health")
      const analytics = await httpGet("http://localhost:8000/health")
      out.push("```")
      out.push(`data-engine        : ${data.ok ? "OK" : "DOWN"} (${data.status}) ${data.body}`)
      out.push(`analytics-engine   : ${analytics.ok ? "OK" : "DOWN"} (${analytics.status}) ${analytics.body}`)
      out.push("```")
      out.push("")
    }

    const brokerUrl =
      expandConfigUrl(process.env.ARGOS_BROKER_URL) ||
      (() => {
        try {
          const cfg = JSON.parse(
            require("fs").readFileSync(path.join(ROOT, "config.json"), "utf8")
          )
          return expandConfigUrl(cfg?.broker?.url)
        } catch {
          return ""
        }
      })()

    out.push("## Broker reachability")
    out.push("```")
    out.push(`URL              : ${brokerUrl || "(not configured)"}`)
    if (brokerUrl) {
      if (commandExists("redis-cli")) {
        out.push(`redis-cli PING   : ${tryRun("redis-cli", ["-u", brokerUrl, "ping"])}`)
      } else {
        const u = new URL(brokerUrl)
        const isUp = await httpGet(`http://${u.hostname}:${u.port || 6379}/`).catch(() => ({
          ok: false,
          status: 0,
          body: "n/a (RESP brokers don't expose HTTP)",
        }))
        out.push(
          `TCP probe        : ${isUp.status > 0 ? `responded (${isUp.status})` : "unreachable or RESP-only (no HTTP)"}`
        )
      }
    }
    out.push("```")
    out.push("")

    out.push("## Base files")
    for (const m of fileMarkers) {
      const p = path.join(ROOT, m.rel)
      out.push(`- ${existsSync(p) ? "OK" : "MISSING"}  ${m.label}  (${m.rel})`)
    }
    out.push("")

    return { title: "health_check", output: out.join("\n") }
  },
})

export const tick_rate = tool({
  description:
    "Sample XLEN of a broker stream 5 times over ~2s and report the items/second rate. Use to gauge tick-pipeline backpressure.",
  args: {
    stream: tool.schema.string().describe("Stream name, e.g. ticks:btcusdt"),
    samples: tool.schema
      .number()
      .describe("Number of samples to take")
      .default(5),
    interval_ms: tool.schema
      .number()
      .describe("Delay between samples in milliseconds")
      .default(500),
  },
  async execute({ stream, samples, interval_ms }) {
    const sleep = (ms: number) =>
      new Promise<void>((r) => setTimeout(r, ms))
    const counts: number[] = []
    for (let i = 0; i < samples; i++) {
      const r = tryRun("redis-cli", ["XLEN", stream])
      const n = Number(r.replace(/[^\d-]/g, ""))
      counts.push(Number.isFinite(n) ? n : 0)
      if (i < samples - 1) await sleep(interval_ms)
    }
    const delta = counts[counts.length - 1] - counts[0]
    const elapsedSec = (interval_ms * (samples - 1)) / 1000
    const rate = elapsedSec > 0 ? delta / elapsedSec : 0
    return {
      title: `XLEN ${stream}`,
      output:
        `Samples: ${counts.join(", ")}\n` +
        `Delta: ${delta} items in ${elapsedSec.toFixed(2)}s\n` +
        `Rate: ${rate.toFixed(2)} items/sec`,
    }
  },
})
