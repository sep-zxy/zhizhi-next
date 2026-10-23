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

## Pi request-schema boundary regression

The production Pi bridge previously generated a root `oneOf` without `type: object`.
The production Codex runner's pre-execution schema gate requires that explicit root
type. A no-model reproduction through both adapters therefore returned
`REQUEST_SCHEMA_INVALID` after one reservation and before any executor call. That
early error was also absent from the report allowlist and carried no `ModelFailure`,
producing `OTHER` with empty model-failure counts. This reproduction establishes a
source-level defect, not the historical error code of any Windows run.

The bridge now declares the object root while retaining every closed variant,
discriminator and tool-argument schema. The global validator and secret scanner
are unchanged. The CLI still receives the existing strict `result_json` string
envelope; Pi's inner schema is validated separately. There is no output-shape or
wire-protocol change. The three early errors `REQUEST_SCHEMA_INVALID`,
`SECRET_INPUT_FORBIDDEN`, and `NO_APPROVED_SUBSCRIPTION_ROUTE` now carry the existing
closed `validation / policy / policy_rejected` diagnostic and remain distinguishable
by fixed error code. Unknown errors are still redacted.

Regression coverage uses production bridge and runner classes with a synthetic
executor and an explicit process-spawn prohibition. It checks the old schema's
zero-executor failure, both tool/text turns after the fix, unchanged closed-union
acceptance, all three early guards, provider propagation, and final report
projection. This is fixture integration, not a real subscription or Windows E2E.
