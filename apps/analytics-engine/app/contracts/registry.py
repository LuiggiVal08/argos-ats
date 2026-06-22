"""Contract registry — loads JSON schemas from project root contracts/."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal


# ── Schema field descriptor ────────────────────────────────────────

@dataclass(frozen=True)
class FieldDef:
    type: str
    required: bool = False
    pattern: str | None = None
    enum: tuple[str, ...] | None = None
    min: float | None = None
    max: float | None = None
    const: int | str | bool | None = None
    default: Any = None
    description: str = ""


@dataclass(frozen=True)
class SchemaDef:
    stream: str
    version: int
    fields: dict[str, FieldDef]


# ── Loader ─────────────────────────────────────────────────────────

_CONTRACTS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "contracts")
)

_SOURCES: dict[str, str] = {
    "candle":    "market-candles.schema.json",
    "signal":    "signals-trading.schema.json",
    "order":     "orders-execution.schema.json",
    "fill":      "fills-execution.schema.json",
    "heartbeat": "system-heartbeat.schema.json",
    "error":     "system-errors.schema.json",
}


def _load_raw(name: str) -> dict[str, Any]:
    path = os.path.join(_CONTRACTS_DIR, _SOURCES[name])
    with open(path) as f:
        return json.load(f)


def _parse_field(name: str, raw: dict[str, Any]) -> FieldDef:
    return FieldDef(
        type=raw["type"],
        required=raw.get("required", False),
        pattern=raw.get("pattern"),
        enum=tuple(raw["enum"]) if "enum" in raw else None,
        min=raw.get("min"),
        max=raw.get("max"),
        const=raw.get("const"),
        default=raw.get("default"),
        description=raw.get("description", ""),
    )


_SCHEMAS: dict[str, SchemaDef] = {}

for key in _SOURCES:
    raw = _load_raw(key)
    fields = {fn: _parse_field(fn, fd) for fn, fd in raw["fields"].items()}
    _SCHEMAS[key] = SchemaDef(
        stream=raw["stream"],
        version=raw["version"],
        fields=fields,
    )


# ── Validator ──────────────────────────────────────────────────────

@dataclass
class ValidationError:
    field: str
    rule: str
    expected: str
    actual: str


def _validate_value(value: Any, field_name: str, defn: FieldDef) -> list[ValidationError]:
    errors: list[ValidationError] = []

    # optional with default
    if value is None:
        if defn.required:
            errors.append(ValidationError(field_name, "required", "present", "None"))
        return errors

    # type
    if defn.type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(ValidationError(field_name, "type", "integer", type(value).__name__))
    elif defn.type == "number":
        if not isinstance(value, (int, float)):
            errors.append(ValidationError(field_name, "type", "number", type(value).__name__))
    elif defn.type == "boolean":
        if not isinstance(value, bool):
            errors.append(ValidationError(field_name, "type", "boolean", type(value).__name__))
    elif defn.type == "object":
        if not isinstance(value, dict):
            errors.append(ValidationError(field_name, "type", "object", type(value).__name__))
    elif defn.type == "string":
        if not isinstance(value, str):
            errors.append(ValidationError(field_name, "type", "string", type(value).__name__))

    # const
    if defn.const is not None and value != defn.const:
        errors.append(ValidationError(field_name, "const", str(defn.const), str(value)))

    # enum
    if defn.enum is not None and value not in defn.enum:
        errors.append(ValidationError(field_name, "enum", "|".join(defn.enum), str(value)))

    # pattern
    if defn.pattern and isinstance(value, str):
        if not re.match(defn.pattern, value):
            errors.append(ValidationError(field_name, "pattern", defn.pattern, value))

    # min / max
    if defn.min is not None and isinstance(value, (int, float)) and value < defn.min:
        errors.append(ValidationError(field_name, "min", f">={defn.min}", str(value)))
    if defn.max is not None and isinstance(value, (int, float)) and value > defn.max:
        errors.append(ValidationError(field_name, "max", f"<={defn.max}", str(value)))

    return errors


def validate(schema_key: str, payload: dict[str, Any]) -> list[ValidationError]:
    """Validate *payload* against the schema identified by *schema_key*.

    Returns an empty list on success.
    """
    schema = _SCHEMAS.get(schema_key)
    if schema is None:
        return [ValidationError("_schema", "unknown", list(_SCHEMAS.keys()).__repr__(), schema_key)]

    errors: list[ValidationError] = []
    for field_name, defn in schema.fields.items():
        errors.extend(_validate_value(payload.get(field_name), field_name, defn))
    return errors


# ── Convenience shortcuts ─────────────────────────────────────────

def validate_candle(p: dict[str, Any]) -> list[ValidationError]:
    return validate("candle", p)

def validate_signal(p: dict[str, Any]) -> list[ValidationError]:
    return validate("signal", p)

def validate_order(p: dict[str, Any]) -> list[ValidationError]:
    return validate("order", p)

def validate_fill(p: dict[str, Any]) -> list[ValidationError]:
    return validate("fill", p)

def validate_heartbeat(p: dict[str, Any]) -> list[ValidationError]:
    return validate("heartbeat", p)

def validate_error(p: dict[str, Any]) -> list[ValidationError]:
    return validate("error", p)
