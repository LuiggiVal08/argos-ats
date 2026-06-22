export interface ExchangePosition {
  symbol: string;
  side: string;
  quantity: number;
  entryPrice: number;
  currentPrice: number;
}

export interface PositionRepository {
  saveSnapshot(snapshot: { exchangeConnected: boolean; lastTickId: string; lastTickTimestamp: number }): Promise<void>;
  loadSnapshot(): Promise<{ exchangeConnected: boolean; lastTickId: string; lastTickTimestamp: number } | null>;
  clear(): Promise<void>;
}
