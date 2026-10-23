# Pi failure diagnostics, not an acceptance relaxation

A failed R24 journey ended with one assistant error, no tool execution, valid outer JSONL, and an `event_validation` failure. That phase is the final journey assertion, not proof that model JSON parsing failed. The actual inner error was lost; it must not be retrospectively guessed.

The previous reporting path passed adapter error codes through Python and TypeScript, but its final fixed allowlist omitted model-runner codes. Consequently schema, incomplete-turn, deadline, and other unrelated failures all became `OTHER`. Failed model results were not successful receipts. The bridge also omitted the existing sanitized `ModelFailure` from its response.

This diagnostic-only change:

- Preserves enumerated adapter-owned error codes in the final summary. Unknown codes remain `OTHER`.
- Adds an optional failed-response `failure` object with exactly `stage`, `kind`, and `summary`, each checked against the existing closed runner vocabulary.
- Projects those values into fixed counter dictionaries. No exception text, model output, prompt, notice body, arbitrary JSON/schema paths, or credentials are included.
- Rejects unknown failure keys/values and forbids failure metadata on successful or ready responses.
- Does not change event acceptance, model/tool budgets, retry behavior, identity, authorization, receipt requirements, or release integrity checks.

The current runner still combines envelope parsing, embedded result JSON parsing, output schema validation and secret-like output rejection into `OUTPUT_SCHEMA_INVALID` / `output_validation_failed`. This patch does not manufacture finer distinctions that the runner does not currently retain. It does not recover the previous R24 error, and fixture test success is not real end-to-end success.
