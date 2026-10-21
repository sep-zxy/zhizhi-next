/** These inputs are captured from real temporary SQLite/Git + ASGI, not hand-authored fixtures. */
import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";
import { WikiApi } from "../../src/api";
type Capture = { status: number; body: Record<string, unknown> };
const captured = JSON.parse(
  readFileSync("test-results/domain-contract.json", "utf8"),
) as Record<string, Capture>;
const api = (name: string) =>
  new WikiApi(
    async () =>
      new Response(JSON.stringify(captured[name].body), {
        status: captured[name].status,
      }),
  );
describe("real domain ASGI capture → private Wiki parser", () => {
  it("rejects the real unauthenticated response", async () => {
    await expect(api("unauthenticated").entries()).rejects.toMatchObject({
      status: 401,
    });
  });
  it("reads a real Git-backed canonical entry and historical revision", async () => {
    const entries = await api("entries").entries();
    expect(entries.data).toHaveLength(1);
    const entry = await api("entry").entry(entries.data[0].entry_id);
    expect(entry.data.body).toBe("Evidence says addition preserves history.\n");
    expect(
      (await api("history").entry(entry.data.entry_id, 1)).data.revision,
    ).toBe(1);
  });
  it("reads a real formal-Wiki search projection", async () => {
    expect(
      (await api("search").search("Evidence")).data.length,
    ).toBeGreaterThan(0);
  });
  it("reads task pagination, waiting-auth status and events without internal payloads", async () => {
    expect((await api("tasks").tasks()).data.has_more).toBe(false);
    const task = (await api("task").task("ignored")).data;
    expect(task.state).toBe("waiting_auth");
    expect(JSON.stringify(task)).not.toContain("never display");
    expect(
      (await api("events").taskEvents(task.task_id)).data.length,
    ).toBeGreaterThan(0);
  });
  it("reads actual intake list/detail projection shapes", async () => {
    expect((await api("intakes").intakes()).data.items).toHaveLength(1);
    expect((await api("intake").intake("ignored")).data.state).toBe("retained");
  });
  it("reads exact proposal and challenge fields used by approval UI", async () => {
    expect((await api("reviews").reviews()).data).toHaveLength(1);
    const change = (await api("change").change("ignored")).data;
    const challenge = (await api("challenge").challenge(change.change_id)).data;
    expect(challenge).toMatchObject({
      change_id: change.change_id,
      patch_hash: change.patch_hash,
      base_revision: change.base_revision,
    });
  });
  it("reads actual capability and health reports without assuming account readiness", async () => {
    expect(
      (await api("capabilities").capabilities()).data.length,
    ).toBeGreaterThan(0);
    expect(await api("health").health()).toMatchObject({
      worker_enabled: false,
      publication_enabled: false,
    });
  });
});

describe("real versioned action contracts", () => {
  it("reads source and actual learning words with reference binding", async () => {
    const source = captured.evidence.body.data as {
      source_id: string;
      revision: number;
      content_hash: string;
    };
    const evidence = await api("evidence").evidence({
      source_id: source.source_id,
      source_revision: source.revision,
      content_hash: source.content_hash,
    });
    expect(evidence.data.text).toBe(
      "Evidence says addition preserves history.",
    );
    const learning = captured.learning_evidence.body.data as {
      outcome_id: string;
      content_hash: string;
    };
    expect(
      (
        await api("learning_evidence").evidence({
          learning_outcome_id: learning.outcome_id,
          content_hash: learning.content_hash,
        })
      ).data.text,
    ).toBe("Synthetic user's own words.");
  });
  it("validates real resume and cancel receipts without reporting task completion", async () => {
    const original = captured.task.body.data as {
      task_id: string;
      revision: number;
    };
    expect(
      (
        await api("task_resume").controlTask(
          original.task_id,
          "resume",
          original.revision,
          "fixture-key",
        )
      ).data.state,
    ).toBe("queued");
    expect(
      (
        await api("task_cancel").controlTask(
          original.task_id,
          "cancel",
          original.revision + 1,
          "fixture-key",
        )
      ).data.state,
    ).toBe("cancelled");
  });
  it("validates supplement revision and keeps note out of the reading body", async () => {
    const intake = captured.new_intake.body.data as { intake_id: string };
    const result = await api("supplement").supplementIntake(
      intake.intake_id,
      {
        expected_preview_revision: 0,
        text: "Supplemented synthetic material.",
        private_note: "Synthetic private note, not source text.",
      },
      "fixture-key",
    );
    expect(result.data.state).toBe("partial");
    expect(result.data.preview_revision).toBe(1);
    expect(result.data.preview?.document).toMatchObject({
      body_text: "Supplemented synthetic material.",
      body_complete: false,
    });
    expect(result.data.source_id).toBeUndefined();
  });
  it("validates an actual uploaded byte receipt without exposing base64 or mixing annotations", async () => {
    const intake = captured.new_intake.body.data as { intake_id: string };
    const result = await api("text_attachment").uploadTextAttachment(
      intake.intake_id,
      {
        expected_preview_revision: 1,
        filename: "synthetic.md",
        content_type: "text/markdown",
        content_base64: Buffer.from("\uFEFF# Synthetic file\r\n").toString(
          "base64",
        ),
        private_note: "Separate synthetic file note.",
      },
      "fixture-key",
    );
    expect(result.data.preview_revision).toBe(2);
    expect(result.data.source_id).toBeUndefined();
    expect(result.data.preview?.document).toMatchObject({
      body_text: "\uFEFF# Synthetic file\r\n",
      body_complete: false,
    });
    expect(JSON.stringify(result.data)).not.toContain("content_base64");
    expect(JSON.stringify(result.data.preview?.document)).not.toContain(
      "Separate synthetic file note.",
    );
  });
  it("parses an independently queued user proposal rather than an applied entry", async () => {
    const result = await api("user_proposal").proposeChange({}, "fixture-key");
    expect(result.data.base_revision).toBe(1);
    expect(result.data.operation).toBe("update");
    expect(result.data.change_id).toBeTruthy();
    expect(result.task_id).toBeTruthy();
    expect(result.data.state).not.toBe("applied");
  });
});
