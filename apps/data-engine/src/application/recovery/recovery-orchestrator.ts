import { SnapshotRepository, SystemSnapshot } from "../ports/snapshot-repository.port";
import { CandleStore } from "../ports/candle-store.port";
import { TickBuffer } from "../ports/tick-buffer.port";
import { Symbol } from "../../domain/value-objects/symbol";
import { Timeframe } from "../../domain/value-objects/timeframe";

export enum RecoveryStatus {
  SAFE = "SAFE",
  DEGRADED = "DEGRADED",
  BLOCKED = "BLOCKED",
}

export interface RecoveryReport {
  timestamp: number;
  status: RecoveryStatus;
  snapshotLoaded: boolean;
  candleStatesRestored: number;
  errors: string[];
}

export class RecoveryOrchestrator {
  constructor(
    private readonly snapshotRepo: SnapshotRepository,
    private readonly candleStore: CandleStore,
    private readonly tickBuffer: TickBuffer,
  ) {}

  async recover(): Promise<RecoveryReport> {
    const errors: string[] = [];
    let snapshot: SystemSnapshot | null = null;
    let candleStatesRestored = 0;

    try {
      snapshot = await this.snapshotRepo.loadLatest();
    } catch (err) {
      errors.push(`snapshot_load_failed: ${err}`);
    }

    if (snapshot) {
      try {
        const entries = Object.entries(snapshot.candleStates);
        for (const [key, state] of entries) {
          const [sym, tfStr] = key.split("::");
          if (sym && tfStr) {
            await this.candleStore.set(Symbol.parse(sym), Timeframe.parse(tfStr), state);
            candleStatesRestored++;
          }
        }
      } catch (err) {
        errors.push(`candle_state_restore_failed: ${err}`);
      }
    }

    const status = snapshot
      ? errors.length === 0
        ? RecoveryStatus.SAFE
        : RecoveryStatus.DEGRADED
      : RecoveryStatus.DEGRADED;

    return {
      timestamp: Date.now(),
      status,
      snapshotLoaded: snapshot !== null,
      candleStatesRestored,
      errors,
    };
  }
}
