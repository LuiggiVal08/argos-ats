import { Controller, Get, Inject } from "@nestjs/common"
import { ConfigService } from "@nestjs/config"
import { BUS, EXCHANGE_GATEWAY } from "../config/tokens"
import { RedisProtocolBus } from "../messaging/redis-protocol-bus"
import { ExchangeGateway } from "../../application/ports/exchange-gateway.port"

const STALE_TICK_MS = 10_000

@Controller("health")
export class HealthController {
  constructor(
    private readonly config: ConfigService,
    @Inject(BUS) private readonly bus: RedisProtocolBus,
    @Inject(EXCHANGE_GATEWAY)
    private readonly exchange: ExchangeGateway,
  ) {}

  @Get()
  async health(): Promise<{
    status: "ok" | "degraded"
    mode: string
    broker: boolean
  }> {
    const brokerOk = await this.bus.ping()
    return {
      status: brokerOk ? "ok" : "degraded",
      mode: this.config.get<string>("ENVIRONMENT_MODE", "PAPER_TRADING"),
      broker: brokerOk,
    }
  }

  @Get("exchange")
  exchangeHealth(): {
    state: string
    connected: boolean
    lastTickAgeMs: number | null
    stale: boolean
    reconnectAttempt: number
    totalReconnects: number
    connectedAt: number | null
  } {
    const state = this.exchange.state()
    const connected = state === "open"
    const lastTickAgeMs = this.exchange.lastTickAgeMs
    const stale =
      connected && lastTickAgeMs !== null && lastTickAgeMs > STALE_TICK_MS
    const info = this.exchange.exchangeInfo()
    return {
      state,
      connected,
      lastTickAgeMs,
      stale,
      reconnectAttempt: info.reconnectAttempt,
      totalReconnects: info.totalReconnects,
      connectedAt: info.connectedAt,
    }
  }
}
