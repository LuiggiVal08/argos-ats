import * as fs from "fs"
import * as path from "path"
import Database from "better-sqlite3"
import { HistoricalEvent } from "../../domain/entities/historical-event"
import { EventStore } from "../../application/ports/event-store.port"

export interface SqliteEventStoreOptions {
  dbPath: string
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS events (
  kind         TEXT NOT NULL,
  symbol       TEXT NOT NULL,
  timestamp_ms INTEGER NOT NULL,
  day          TEXT NOT NULL,
  payload      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_events_day ON events(day);
`

const INSERT_SQL = `INSERT INTO events (kind, symbol, timestamp_ms, day, payload) VALUES (?, ?, ?, ?, ?)`

function toDay(ts: number): string {
  return new Date(ts).toISOString().slice(0, 10)
}

function extractTs(event: HistoricalEvent): number {
  if (event.kind === "tick") return event.data.ts
  if (event.kind === "candle") return event.data.openTs
  return event.data.timestamp
}

function extractSymbol(event: HistoricalEvent): string {
  if (event.kind === "tick") return event.data.symbol
  if (event.kind === "candle") return event.data.symbol
  return event.data.symbol
}

export class SqliteEventStore implements EventStore {
  private readonly db: Database.Database
  private readonly insert: Database.Statement

  constructor(opts: SqliteEventStoreOptions) {
    const dir = path.dirname(opts.dbPath)
    fs.mkdirSync(dir, { recursive: true })
    Database(":memory:").constructor.prototype
    this.db = new Database(opts.dbPath)
    this.db.pragma("journal_mode = WAL")
    this.db.pragma("synchronous = NORMAL")
    this.db.exec(SCHEMA)
    this.insert = this.db.prepare(INSERT_SQL)
  }

  async store(event: HistoricalEvent): Promise<void> {
    const ts = extractTs(event)
    const day = toDay(ts)
    const symbol = extractSymbol(event)
    const payload = JSON.stringify(event.data)
    this.insert.run(event.kind, symbol, ts, day, payload)
  }

  async storeBatch(events: HistoricalEvent[]): Promise<void> {
    const txn = this.db.transaction((items: HistoricalEvent[]) => {
      for (const event of items) {
        const ts = extractTs(event)
        const day = toDay(ts)
        const symbol = extractSymbol(event)
        const payload = JSON.stringify(event.data)
        this.insert.run(event.kind, symbol, ts, day, payload)
      }
    })
    txn(events)
  }

  async *read(
    kind: HistoricalEvent["kind"],
    fromTs: number,
    toTs: number,
  ): AsyncIterable<HistoricalEvent> {
    const rows = this.db
      .prepare(
        "SELECT kind, payload FROM events WHERE kind = ? AND timestamp_ms >= ? AND timestamp_ms <= ? ORDER BY timestamp_ms ASC",
      )
      .all(kind, fromTs, toTs) as Array<{ kind: string; payload: string }>

    for (const row of rows) {
      yield { kind: row.kind as HistoricalEvent["kind"], data: JSON.parse(row.payload) }
    }
  }

  async close(): Promise<void> {
    this.db.close()
  }
}
