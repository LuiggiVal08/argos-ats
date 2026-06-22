import { writeFileSync, renameSync, existsSync, mkdirSync } from "fs"
import { dirname, resolve } from "path"

export interface Checkpoint {
  last_processed_timestamp: number
  last_candle_timestamp: number
  last_signal_id: string | null
  last_order_id: string | null
  last_fill_id: string | null
  open_positions: string
  equity: number
  mode: string
  engine: "data-engine"
  schema_version: 1
}

export class CheckpointWriter {
  private readonly filePath: string
  private intervalHandle: ReturnType<typeof setInterval> | null = null

  constructor(
    filePath?: string,
    private readonly intervalMs = 30_000,
  ) {
    this.filePath = filePath ?? resolve(process.cwd(), "state", "checkpoint.de.json")
  }

  async write(data: Checkpoint): Promise<void> {
    const dir = dirname(this.filePath)
    if (!existsSync(dir)) {
      mkdirSync(dir, { recursive: true })
    }

    const tmpPath = `${this.filePath}.tmp`
    const json = JSON.stringify(data, null, 2)

    try {
      writeFileSync(tmpPath, json, "utf8")
      renameSync(tmpPath, this.filePath)
    } catch {
      console.warn("[checkpoint] write failed", new Error().stack)
    }
  }

  async start(dataProvider: () => Checkpoint): Promise<void> {
    if (this.intervalHandle) return
    this.intervalHandle = setInterval(async () => {
      try {
        await this.write(dataProvider())
      } catch {
        console.warn("[checkpoint] periodic write failed")
      }
    }, this.intervalMs)
  }

  stop(): void {
    if (this.intervalHandle) {
      clearInterval(this.intervalHandle)
      this.intervalHandle = null
    }
  }
}
