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
