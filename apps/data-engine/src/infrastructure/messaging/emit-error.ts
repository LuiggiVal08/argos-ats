import Redis from "ioredis"
import { randomUUID } from "crypto"
import { ValidationError, validateError } from "../../contracts"

export type ErrorSeverity = "WARN" | "ERROR" | "CRITICAL"

export interface ErrorPayload {
  error_id: string
  service: "data-engine" | "analytics-engine"
  severity: ErrorSeverity
  error_code: string
  message: string
  metadata?: Record<string, unknown>
  timestamp: number
  schema_version: number
}

export function buildErrorPayload(
  code: string,
  message: string,
  severity: ErrorSeverity = "ERROR",
  metadata?: Record<string, unknown>,
): ErrorPayload {
  return {
    error_id: randomUUID(),
    service: "data-engine",
    severity,
    error_code: code,
    message,
    metadata,
    timestamp: Date.now(),
    schema_version: 1,
  }
}

export async function emitError(
  client: Redis,
  payload: ErrorPayload,
): Promise<void> {
  const errResult = validateError(payload as unknown as Record<string, unknown>)
  if (!errResult.ok) {
    return
  }
  try {
    await client.xadd("system:errors", "*", "p", JSON.stringify(payload))
  } catch {
    console.warn("[emit-error] failed to publish error event")
  }
}

export function formatValidationErrors(errors: ValidationError[]): string {
  return errors.map((e) => `${e.field}:${e.rule}`).join(" ")
}
