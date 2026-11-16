"""Explicit development-only runners; never silently substituted for a real provider."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .contracts import ModelResult, ModelTask, canonical_hash
from .validation import OUTPUT_ERRORS, parse_output, validate_schema

if TYPE_CHECKING:
    from collections.abc import Mapping


class DisabledModelRunner:
    """Safe default when model processing or the subscription host is unavailable."""

    async def run(self, task: ModelTask) -> ModelResult:
        return ModelResult(
            task_id=task.task_id,
            status="blocked",
            input_fingerprint=task.fingerprint,
            error_code="MODEL_NOT_CONFIGURED",
        )


class FixtureModelRunner:
    """Deterministic test adapter requiring an explicit development opt-in."""

    def __init__(
        self, outputs: Mapping[str, dict[str, Any] | ModelResult], *, development: bool = False
    ) -> None:
        if not development:
            raise ValueError("FixtureModelRunner requires development=True")
        self.outputs = dict(outputs)
        self.calls: list[ModelTask] = []

    async def run(self, task: ModelTask) -> ModelResult:
        self.calls.append(task)
        value = self.outputs.get(task.schema_name)
        if isinstance(value, ModelResult):
            return replace(
                value, task_id=task.task_id, fixture=True, input_fingerprint=task.fingerprint
            )
        if value is None:
            return ModelResult(
                task_id=task.task_id, status="blocked", fixture=True, error_code="FIXTURE_NOT_FOUND"
            )
        try:
            validate_schema(task.output_schema)
            output = parse_output(json.dumps(value), task.output_schema)
        except OUTPUT_ERRORS:
            return ModelResult(
                task_id=task.task_id,
                status="invalid_output",
                fixture=True,
                error_code="OUTPUT_SCHEMA_INVALID",
            )
        return ModelResult(
            task_id=task.task_id,
            status="succeeded",
            output=output,
            provider_id="fixture",
            model_id="fixture",
            fixture=True,
            input_fingerprint=task.fingerprint,
            result_hash=canonical_hash(output),
        )
