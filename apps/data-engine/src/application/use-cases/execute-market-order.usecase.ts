import { validateOrder, validateFill } from "../../contracts"
import { ValidationError } from "../../contracts"

export interface ExchangeGateway {
  executeMarketOrder(params: {
    symbol: string
    side: "BUY" | "SELL"
    amount: string
    idempotencyKey: string
  }): Promise<{
    fillId: string
    orderId: string
    filledQty: string
    avgPrice: string
    status: "FILLED" | "PARTIALLY_FILLED" | "REJECTED"
    exchangeOrderId?: string
    errorMessage?: string
  }>
}

export interface OrderStore {
  save(payload: Record<string, unknown>): Promise<void>
}

export interface FillStore {
  save(payload: Record<string, unknown>): Promise<void>
}

export interface ExecutionErrorHandler {
  emit(code: string, message: string, metadata?: Record<string, unknown>): Promise<void>
}

export type ValidationErrorFormatter = (errors: ValidationError[]) => string

export interface ExecuteMarketOrderUseCase {
  execute(order: Record<string, unknown>): Promise<{ ok: boolean; error?: string }>
}

export class ExecuteMarketOrderUseCaseImpl implements ExecuteMarketOrderUseCase {
  constructor(
    private readonly exchange: ExchangeGateway,
    private readonly orderStore: OrderStore,
    private readonly fillStore: FillStore,
    private readonly errorHandler: ExecutionErrorHandler,
    private readonly formatErrors: ValidationErrorFormatter,
  ) {}

  async execute(order: Record<string, unknown>): Promise<{ ok: boolean; error?: string }> {
    const orderResult = validateOrder(order)
    if (!orderResult.ok) {
      await this.errorHandler.emit(
        "EXECUTION_ORDER_VALIDATION_FAILED",
        `Order rejected before execution: ${this.formatErrors(orderResult.errors)}`,
        { order },
      )
      return { ok: false, error: "order_validation_failed" }
    }

    await this.orderStore.save(order)

    try {
      const exchangeResult = await this.exchange.executeMarketOrder({
        symbol: order.symbol as string,
        side: order.side as "BUY" | "SELL",
        amount: order.amount as string,
        idempotencyKey: order.idempotency_key as string,
      })

      const fillPayload: Record<string, unknown> = {
        fill_id: exchangeResult.fillId,
        order_id: exchangeResult.orderId,
        symbol: order.symbol as string,
        side: order.side as string,
        filled_qty: exchangeResult.filledQty,
        avg_price: exchangeResult.avgPrice,
        status: exchangeResult.status,
        exchange_order_id: exchangeResult.exchangeOrderId,
        error_message: exchangeResult.errorMessage,
        timestamp: Date.now(),
        schema_version: 1,
      }

      const fillResult = validateFill(fillPayload)
      if (!fillResult.ok) {
        await this.errorHandler.emit(
          "EXECUTION_FILL_VALIDATION_FAILED",
          `Fill validation failed after execution: ${this.formatErrors(fillResult.errors)}`,
          { fill: fillPayload },
        )
        return { ok: false, error: "fill_validation_failed" }
      }

      await this.fillStore.save(fillPayload)
      return { ok: true }
    } catch (err) {
      await this.errorHandler.emit(
        "EXECUTION_EXCHANGE_FAILED",
        `Exchange execution failed: ${String(err)}`,
        { order },
      )
      return { ok: false, error: "exchange_execution_failed" }
    }
  }
}
