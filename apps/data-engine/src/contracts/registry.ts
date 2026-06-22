/**
 * Contract registry — loads JSON schemas from the single source of truth
 * at project root `contracts/` and provides typed validators.
 *
 * IMPORTANT: schemas are loaded at module init time and cached. This is
 * acceptable because contracts change only across deployments, not at runtime.
 */

import candleSchema from "../../../../contracts/market-candles.schema.json"
import signalSchema from "../../../../contracts/signals-trading.schema.json"
import orderSchema from "../../../../contracts/orders-execution.schema.json"
import fillSchema from "../../../../contracts/fills-execution.schema.json"
import heartbeatSchema from "../../../../contracts/system-heartbeat.schema.json"
import errorSchema from "../../../../contracts/system-errors.schema.json"

// ── 1. Field-level type info ──────────────────────────────────────

type FieldType = "string" | "number" | "integer" | "boolean" | "object"
type FieldDef = {
  type: FieldType
  required: boolean
  pattern?: string
  enum?: readonly string[]
  min?: number
  max?: number
  const?: number | string | boolean
  default?: unknown
  description?: string
}
type SchemaDef = {
  stream: string
  version: number
  fields: Record<string, FieldDef>
}

// ── 2. Load & validate schema structure ───────────────────────────

function loadSchema(raw: unknown, name: string): SchemaDef {
  const s = raw as Record<string, unknown>
  if (!s.stream || typeof s.stream !== "string") throw new Error(`${name}: missing stream`)
  if (!s.fields || typeof s.fields !== "object") throw new Error(`${name}: missing fields`)
  return s as unknown as SchemaDef
}

const SCHEMAS: Record<string, SchemaDef> = {
  candle:    loadSchema(candleSchema, "market-candles"),
  signal:    loadSchema(signalSchema, "signals-trading"),
  order:     loadSchema(orderSchema, "orders-execution"),
  fill:      loadSchema(fillSchema, "fills-execution"),
  heartbeat: loadSchema(heartbeatSchema, "system-heartbeat"),
  error:     loadSchema(errorSchema, "system-errors"),
}

// ── 3. Validator engine ───────────────────────────────────────────

export interface ValidationError {
  field: string
  rule: string
  expected: string
  actual: string
}

export type ValidationResult =
  | { ok: true }
  | { ok: false; errors: ValidationError[] }

function validateValue(
  value: unknown,
  def: FieldDef,
  fieldPath: string,
): ValidationError | null {
  // -- optional with default --------------------------------------------------
  if (value === undefined || value === null) {
    if (def.required) {
      return { field: fieldPath, rule: "required", expected: "present", actual: String(value) }
    }
    return null
  }

  // -- type -------------------------------------------------------------------
  const actualType = typeof value
  if (def.type === "integer") {
    if (!Number.isInteger(value)) {
      return { field: fieldPath, rule: "type", expected: "integer", actual: actualType }
    }
  } else if (def.type === "number") {
    if (actualType !== "number") {
      return { field: fieldPath, rule: "type", expected: "number", actual: actualType }
    }
  } else if (actualType !== def.type) {
    return { field: fieldPath, rule: "type", expected: def.type, actual: actualType }
  }

  // -- const ------------------------------------------------------------------
  if (def.const !== undefined && value !== def.const) {
    return { field: fieldPath, rule: "const", expected: String(def.const), actual: String(value) }
  }

  // -- enum -------------------------------------------------------------------
  if (def.enum && !def.enum.includes(value as string)) {
    return { field: fieldPath, rule: "enum", expected: def.enum.join("|"), actual: String(value) }
  }

  // -- pattern ----------------------------------------------------------------
  if (def.pattern && typeof value === "string") {
    const re = new RegExp(def.pattern)
    if (!re.test(value)) {
      return { field: fieldPath, rule: "pattern", expected: def.pattern, actual: value }
    }
  }

  // -- min/max ----------------------------------------------------------------
  if (def.min !== undefined && typeof value === "number" && value < def.min) {
    return { field: fieldPath, rule: "min", expected: `>=${def.min}`, actual: String(value) }
  }
  if (def.max !== undefined && typeof value === "number" && value > def.max) {
    return { field: fieldPath, rule: "max", expected: `<=${def.max}`, actual: String(value) }
  }

  return null
}

export function validatePayload(schemaKey: string, payload: Record<string, unknown>): ValidationResult {
  const schema = SCHEMAS[schemaKey]
  if (!schema) {
    return { ok: false, errors: [{ field: "_schema", rule: "unknown", expected: `known key (${Object.keys(SCHEMAS).join(", ")})`, actual: schemaKey }] }
  }

  const errors: ValidationError[] = []

  for (const [fieldName, def] of Object.entries(schema.fields)) {
    const err = validateValue(payload[fieldName], def, fieldName)
    if (err) errors.push(err)
  }

  return errors.length === 0 ? { ok: true } : { ok: false, errors }
}

// ── 4. Typed helpers ──────────────────────────────────────────────

export interface CandlePayload {
  symbol: string
  timeframe: string
  open: string
  high: string
  low: string
  close: string
  volume: string
  timestamp: number
  is_complete?: boolean
  schema_version: number
}

export interface SignalPayload {
  signal_id: string
  symbol: string
  action: "BUY" | "SELL"
  confidence: number
  model_version?: string
  regime?: "TRENDING" | "RANGING" | "UNKNOWN"
  timestamp: number
  schema_version: number
}

export interface OrderPayload {
  order_id: string
  signal_id: string
  symbol: string
  side: "BUY" | "SELL"
  order_type: "MARKET"
  amount: string
  idempotency_key: string
  timestamp: number
  schema_version: number
}

export interface FillPayload {
  fill_id: string
  order_id: string
  symbol: string
  side: "BUY" | "SELL"
  filled_qty: string
  avg_price: string
  status: "FILLED" | "PARTIALLY_FILLED" | "REJECTED"
  exchange_order_id?: string
  error_message?: string
  timestamp: number
  schema_version: number
}

export interface HeartbeatPayload {
  service: "data-engine" | "analytics-engine"
  status: "healthy" | "degraded" | "halted"
  uptime_seconds: number
  mode: "BACKTESTING" | "PAPER_TRADING" | "LIVE" | "LIVE_SIMULATION"
  broker_ok: boolean
  exchange_ok: boolean
  loops_alive?: number
  timestamp: number
  schema_version: number
}

export interface ErrorPayload {
  error_id: string
  service: "data-engine" | "analytics-engine"
  severity: "WARN" | "ERROR" | "CRITICAL"
  error_code: string
  message: string
  metadata?: Record<string, unknown>
  timestamp: number
  schema_version: number
}

export function validateCandle(p: Record<string, unknown>): ValidationResult {
  return validatePayload("candle", p)
}
export function validateSignal(p: Record<string, unknown>): ValidationResult {
  return validatePayload("signal", p)
}
export function validateOrder(p: Record<string, unknown>): ValidationResult {
  return validatePayload("order", p)
}
export function validateFill(p: Record<string, unknown>): ValidationResult {
  return validatePayload("fill", p)
}
export function validateHeartbeat(p: Record<string, unknown>): ValidationResult {
  return validatePayload("heartbeat", p)
}
export function validateError(p: Record<string, unknown>): ValidationResult {
  return validatePayload("error", p)
}
