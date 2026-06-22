import { tool } from "@opencode-ai/plugin"
import { execFileSync } from "child_process"

const dc = (args: string[]) =>
  execFileSync("docker", ["compose", ...args], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  })

export const docker_ps = tool({
  description:
    "List compose services for argos-ats with status and health. Only use when the stack is deployed via Docker Compose. For bare-metal deployments use health_health_check instead.",
  args: {},
  async execute() {
    return { title: "docker compose ps", output: dc(["ps"]) }
  },
})

export const docker_logs = tool({
  description:
    "Tail the last N lines of a compose service's logs. Only use when the stack is deployed via Docker Compose. For bare-metal deployments read logs from the running process directly.",
  args: {
    service: tool.schema
      .string()
      .describe("Service name, e.g. data-engine or analytics-engine"),
    lines: tool.schema.number().describe("Lines to tail").default(100),
  },
  async execute({ service, lines }) {
    return {
      title: `logs ${service} (-${lines})`,
      output: dc(["logs", "--tail", String(lines), service]),
    }
  },
})

export const docker_restart_service = tool({
  description:
    "Restart a single compose service. Only use when the stack is deployed via Docker Compose. Always asks the user first. For bare-metal deployments restart the process directly.",
  args: {
    service: tool.schema.string().describe("Service name to restart"),
  },
  async execute({ service }, ctx) {
    await ctx.ask({
      permission: "docker compose restart",
      patterns: [service],
      always: [],
      metadata: {},
    })
    return {
      title: `restart ${service}`,
      output: dc(["restart", service]),
    }
  },
})
