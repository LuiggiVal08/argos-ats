import { PositionRepository } from "../../application/ports/position-repository.port";

interface StateRow {
  id: number;
  key: string;
  value: string;
}

export class SQLitePositionRepository implements PositionRepository {
  private db: any | null = null;

  constructor(private readonly dbPath: string = "data/engine-state.db") {}

  private async getDb(): Promise<any> {
    if (this.db) return this.db;
    try {
      const sqlite3 = await import("better-sqlite3");
      this.db = new sqlite3.default(this.dbPath);
      this.db.exec("PRAGMA journal_mode=WAL");
      this.db.exec("PRAGMA synchronous=FULL");
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS engine_state (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          key TEXT UNIQUE NOT NULL,
          value TEXT NOT NULL
        )
      `);
    } catch {
      console.warn("[sqlite] init failed — falling back to in-memory position store");
      this.db = new MemoryPositionRepository();
    }
    return this.db;
  }

  async saveSnapshot(snapshot: { exchangeConnected: boolean; lastTickId: string; lastTickTimestamp: number }): Promise<void> {
    const db = await this.getDb();
    if (db instanceof MemoryPositionRepository) {
      await db.saveSnapshot(snapshot);
      return;
    }
    const stmt = db.prepare(
      "INSERT OR REPLACE INTO engine_state (key, value) VALUES (?, ?)"
    );
    stmt.run("state", JSON.stringify(snapshot));
  }

  async loadSnapshot(): Promise<{ exchangeConnected: boolean; lastTickId: string; lastTickTimestamp: number } | null> {
    const db = await this.getDb();
    if (db instanceof MemoryPositionRepository) {
      return db.loadSnapshot();
    }
    const row: StateRow | undefined = db
      .prepare("SELECT * FROM engine_state WHERE key = ?")
      .get("state");
    if (!row) return null;
    return JSON.parse(row.value);
  }

  async clear(): Promise<void> {
    const db = await this.getDb();
    if (db instanceof MemoryPositionRepository) {
      return db.clear();
    }
    db.exec("DELETE FROM engine_state WHERE key = 'state'");
  }
}

class MemoryPositionRepository implements PositionRepository {
  private state: { exchangeConnected: boolean; lastTickId: string; lastTickTimestamp: number } | null = null;

  async saveSnapshot(snapshot: { exchangeConnected: boolean; lastTickId: string; lastTickTimestamp: number }): Promise<void> {
    this.state = snapshot;
  }

  async loadSnapshot(): Promise<{ exchangeConnected: boolean; lastTickId: string; lastTickTimestamp: number } | null> {
    return this.state;
  }

  async clear(): Promise<void> {
    this.state = null;
  }
}
