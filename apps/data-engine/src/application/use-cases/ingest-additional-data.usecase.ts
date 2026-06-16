import { MessageBus } from "../ports/message-bus.port"
import { StreamName } from "../../domain/value-objects/stream-name"
import { FundingRate } from "../../domain/value-objects/funding-rate"
import { OpenInterest } from "../../domain/value-objects/open-interest"
import { AggTrade } from "../../domain/value-objects/agg-trade"

export class IngestAdditionalDataUseCase {
  constructor(private readonly bus: MessageBus) {}

  async publishFundingRate(fr: FundingRate): Promise<void> {
    const stream = StreamName.parse(`funding:${fr.symbol.toLowerCase()}`)
    await this.bus.publishRaw(stream, fr.toJSON() as unknown as Record<string, unknown>)
  }

  async publishOpenInterest(oi: OpenInterest): Promise<void> {
    const stream = StreamName.parse(`oi:${oi.symbol.toLowerCase()}`)
    await this.bus.publishRaw(stream, oi.toJSON() as unknown as Record<string, unknown>)
  }

  async publishAggTrade(trade: AggTrade): Promise<void> {
    const stream = StreamName.parse(`orderflow:${trade.symbol.toLowerCase()}`)
    await this.bus.publishRaw(stream, trade.toJSON() as unknown as Record<string, unknown>)
  }
}
