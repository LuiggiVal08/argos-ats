import { Candle } from "../../domain/entities/candle";
import { CandleState } from "./candle-store.port";

export interface SystemSnapshot {
  timestamp: number;
  candleStates: Record<string, CandleState>;
  lastTickTimestamp: number;
  streamPrefix: string;
}

export interface SnapshotRepository {
  save(snapshot: SystemSnapshot): Promise<void>;
  loadLatest(): Promise<SystemSnapshot | null>;
  clear(): Promise<void>;
}
