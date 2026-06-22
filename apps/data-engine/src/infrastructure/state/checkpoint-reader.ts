import { readFileSync, existsSync } from "fs"

export class CheckpointReadError extends Error {
  constructor(msg: string) {
    super(msg)
    this.name = "CheckpointReadError"
  }
}

export class CheckpointReader {
  constructor(private readonly filePath: string) {}

  exists(): boolean {
    return existsSync(this.filePath)
  }

  readSync<T>(): T {
    if (!this.exists()) {
      throw new CheckpointReadError(`checkpoint not found: ${this.filePath}`)
    }
    try {
      const raw = readFileSync(this.filePath, "utf8")
      return JSON.parse(raw) as T
    } catch (err) {
      throw new CheckpointReadError(
        `failed to read checkpoint: ${err instanceof Error ? err.message : String(err)}`,
      )
    }
  }

  readWithFallback<T>(fallback: T): T {
    if (!this.exists()) return fallback
    try {
      return this.readSync<T>()
    } catch {
      return fallback
    }
  }
}
