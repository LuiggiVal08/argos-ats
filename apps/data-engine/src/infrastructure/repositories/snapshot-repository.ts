import {
  SnapshotRepository,
  SystemSnapshot,
} from "../../application/ports/snapshot-repository.port";

interface SnapshotRow {
  id: number;
  timestamp: number;
  data: string;
}

export class SQLiteSnapshotRepository implements SnapshotRepository {
  private db: any | null = null;

  constructor(private readonly dbPath: string = "data/engine-snapshots.db") {}

  private async getDb(): Promise<any> {
    if (this.db) return this.db;
    const sqlite3 = await import("better-sqlite3");
    this.db = new sqlite3.default(this.dbPath);
    this.db.exec("PRAGMA journal_mode=WAL");
    this.db.exec("PRAGMA synchronous=FULL");
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS engine_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp INTEGER NOT NULL,
        data TEXT NOT NULL
      )
    `);
    return this.db;
  }

  async save(snapshot: SystemSnapshot): Promise<void> {
    const db = await this.getDb();
    const stmt = db.prepare(
      "INSERT INTO engine_snapshots (timestamp, data) VALUES (?, ?)"
    );
    stmt.run(snapshot.timestamp, JSON.stringify(snapshot));
  }

  async loadLatest(): Promise<SystemSnapshot | null> {
    const db = await this.getDb();
    const row: SnapshotRow | undefined = db
      .prepare("SELECT * FROM engine_snapshots ORDER BY id DESC LIMIT 1")
      .get();
    if (!row) return null;
    const parsed = JSON.parse(row.data);
    return {
      timestamp: parsed.timestamp,
      candleStates: parsed.candleStates,
      lastTickTimestamp: parsed.lastTickTimestamp,
      streamPrefix: parsed.streamPrefix,
    };
  }

  async clear(): Promise<void> {
    const db = await this.getDb();
    db.exec("DELETE FROM engine_snapshots");
  }

  async close(): Promise<void> {
    if (this.db) {
      this.db.close();
      this.db = null;
    }
  }
}
