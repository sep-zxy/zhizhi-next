"""Strict JSON validation with no remote schema resolution or heuristic extraction."""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing.exceptions import Unresolvable

SECRET_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:sk-proj-|sk-live-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}|"
    r'"(?:refresh_token|access_token|client_secret|password|api_key)"\s*:\s*"[^"\s]{6,}"',
    re.IGNORECASE,
)


def contains_obvious_secret(value: Any) -> bool:
    """Defense in depth only; caller classification and data permission remain mandatory."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str) and SECRET_PATTERN.search(item):
            return True
        if isinstance(item, dict):
            # Check key/value pairs, including credential fields split across JSON structure.
            for key, child in item.items():
                if isinstance(child, str) and SECRET_PATTERN.search(json.dumps({key: child})):
                    return True
                stack.append(key)
                stack.append(child)
        elif isinstance(item, list | tuple):
            stack.extend(item)
    return False


def sanitized_usage(value: dict[str, Any]) -> dict[str, Any]:
    """Retain only bounded numeric usage fields, never arbitrary provider metadata/text."""
    token_fields = {
        "input",
        "output",
        "cacheRead",
        "cacheWrite",
        "cacheWrite1h",
        "reasoning",
        "totalTokens",
    }

    def numeric_fields(source: Any, fields: set[str]) -> dict[str, int | float]:
        if not isinstance(source, dict):
            return {}
        return {
            key: number
            for key, number in source.items()
            if key in fields
            and isinstance(number, int | float)
            and not isinstance(number, bool)
            and 0 <= number <= 1e15
        }

    result: dict[str, Any] = numeric_fields(value, token_fields)
    cost = numeric_fields(
        value.get("cost"), {"input", "output", "cacheRead", "cacheWrite", "total"}
    )
    if cost:
        result["cost"] = cost
    return result


def validate_schema(schema: dict[str, Any]) -> None:
    """Admit object schemas and local JSON pointers; never fetch a remote $ref."""

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(child, str) or not child.startswith("#")
                ):
                    raise ValueError("Remote schema references are forbidden")
                if key in {"$id", "$schema"} and key == "$id":
                    raise ValueError("Schema base URI overrides are forbidden")
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    if schema.get("type") != "object":
        raise ValueError("Model output schema must have object type")
    Draft202012Validator.check_schema(schema)


def parse_output(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Require one complete JSON object, no code fences, duplicate keys or NaN."""

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_: str) -> None:
        raise ValueError("Non-finite JSON number")

    value = json.loads(text, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("Model output must be an object")
    Draft202012Validator(schema).validate(value)
    return value


OUTPUT_ERRORS = (ValueError, SchemaError, ValidationError, RecursionError, Unresolvable)
