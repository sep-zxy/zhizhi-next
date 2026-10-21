import { describe, it, expect, vi } from "vitest";
import { WikiApi } from "../../src/api";
const envelope = (data: unknown, status = 200) =>
  new Response(
    JSON.stringify({ data, task_id: null, trace_id: "trace_test" }),
    { status, headers: { "Content-Type": "application/json" } },
  );
describe("same-origin domain transport", () => {
  it("does not let an aborted old request block a newer session", async () => {
    const controller = new AbortController();
    const api = new WikiApi(async () => {
      controller.abort();
      return new Response("{}", { status: 401 });
    });
    await expect(api.entries(0, controller.signal)).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(api.isAccessBlocked()).toBe(false);
  });
  it("uses noncached same-origin reads without generated credentials", async () => {
    const transport = vi.fn().mockResolvedValue(envelope([]));
    const api = new WikiApi(transport);
    await api.entries();
    expect(transport).toHaveBeenCalledOnce();
    const [url, init] = transport.mock.calls[0];
    expect(url).toBe("/api/knowledge/v1/entries?limit=100&offset=0");
    expect(init).toMatchObject({
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
    });
    expect(init.headers).not.toHaveProperty("Authorization");
  });
  it("never substitutes demo data for failed authentication", async () => {
    const api = new WikiApi(
      async () =>
        new Response(
          JSON.stringify({
            error: {
              code: "UNAUTHENTICATED",
              detail: "do not display private raw error",
            },
            trace_id: "trace_test",
          }),
          { status: 401 },
        ),
    );
    await expect(api.entries()).rejects.toMatchObject({
      status: 401,
      code: "UNAUTHENTICATED",
    });
    try {
      await api.entries();
    } catch (e) {
      expect(String(e)).not.toContain("private raw error");
    }
  });
  it("notifies a global private-content boundary once on 401 and resets only on explicit retry", async () => {
    const api = new WikiApi(
      async () => new Response("not json", { status: 401 }),
    );
    const listener = vi.fn();
    const remove = api.subscribeAccess(listener);
    expect(api.isAccessBlocked()).toBe(false);
    await expect(api.entries()).rejects.toBeDefined();
    expect(api.isAccessBlocked()).toBe(true);
    expect(listener).toHaveBeenCalledTimes(1);
    await expect(api.entries()).rejects.toBeDefined();
    expect(listener).toHaveBeenCalledTimes(1);
    api.retryAccess();
    expect(api.isAccessBlocked()).toBe(false);
    expect(listener).toHaveBeenCalledTimes(2);
    remove();
  });
  it("rejects malformed successful envelopes", async () => {
    const api = new WikiApi(
      async () => new Response(JSON.stringify({ items: [] })),
    );
    await expect(api.entries()).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
  });
  it("rejects malformed records rather than showing a successful empty catalog", async () => {
    const api = new WikiApi(async () => envelope([{ title: "incomplete" }]));
    await expect(api.entries()).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
  });
  it("searches only the formal wiki scope and URL-encodes the query", async () => {
    const transport = vi.fn().mockResolvedValue(envelope({ results: [] }));
    await new WikiApi(transport).search("私有 & #");
    expect(transport.mock.calls[0][0]).toBe(
      "/api/knowledge/v1/search?q=%E7%A7%81%E6%9C%89+%26+%23&scope=wiki&limit=100",
    );
  });
  it("uses explicit has_more for owner-filtered task pages", async () => {
    const transport = vi
      .fn()
      .mockResolvedValue(envelope({ items: [], has_more: false }));
    const value = await new WikiApi(transport).tasks(100, "waiting_auth");
    expect(value.data.has_more).toBe(false);
    expect(transport.mock.calls[0][0]).toContain(
      "offset=100&state=waiting_auth",
    );
  });
  it("keeps idempotency on writes and never adds owner fields", async () => {
    const transport = vi
      .fn()
      .mockResolvedValue(envelope({ intake_id: "int_1", state: "received" }));
    await new WikiApi(transport).submitIntake(
      {
        url: "https://example.com",
        requested_action: "preview",
        data_classification: "personal",
      },
      "same-request",
    );
    const init = transport.mock.calls[0][1];
    expect(init.headers["Idempotency-Key"]).toBe("same-request");
    expect(JSON.parse(init.body)).not.toHaveProperty("owner_id");
  });
  it("does not retry uncertain writes automatically", async () => {
    const transport = vi.fn().mockRejectedValue(new Error("network raw error"));
    await expect(
      new WikiApi(transport).decideIntake(
        "int_1",
        { decision: "keep", preview_revision: 2 },
        "key",
      ),
    ).rejects.toMatchObject({ code: "NETWORK_ERROR" });
    expect(transport).toHaveBeenCalledOnce();
  });
  it("preserves abort instead of changing it into a network error", async () => {
    const controller = new AbortController();
    controller.abort();
    const abort = new DOMException("Aborted", "AbortError");
    const api = new WikiApi(async () => {
      throw abort;
    });
    await expect(api.entries(0, controller.signal)).rejects.toBe(abort);
  });
  it("encodes opaque identifiers without arbitrary route injection", async () => {
    const transport = vi
      .fn()
      .mockResolvedValue(
        envelope({ task_id: "id", kind: "test", state: "queued", revision: 1 }),
      );
    await new WikiApi(transport).task("a/b?x=y");
    expect(transport.mock.calls[0][0]).toBe(
      "/api/knowledge/v1/tasks/a%2Fb%3Fx%3Dy",
    );
  });
  it("reads capabilities as an array instead of pretending they are channel accounts", async () => {
    const api = new WikiApi(async () =>
      envelope({
        capabilities: [
          { platform: "douyin", verification: "not_live_verified" },
        ],
      }),
    );
    expect((await api.capabilities()).data).toHaveLength(1);
  });
});

describe("private action receipt integrity", () => {
  it("treats a scrubbed expired supplement replay as expired, not a new successful preview", async () => {
    const api = new WikiApi(async () =>
      envelope({
        intake_id: "int_expired",
        state: "expired",
        preview_revision: 2,
        preview: null,
      }),
    );
    await expect(
      api.supplementIntake(
        "int_expired",
        { expected_preview_revision: 0, text: "synthetic", private_note: "" },
        "same-key",
      ),
    ).rejects.toMatchObject({ code: "PREVIEW_EXPIRED", status: 409 });
  });
  it("rejects a task receipt that does not match the requested revision or state", async () => {
    const api = new WikiApi(async () =>
      envelope({
        task_id: "tsk_1",
        kind: "fixture",
        revision: 2,
        state: "succeeded",
      }),
    );
    await expect(
      api.controlTask("tsk_1", "resume", 1, "key"),
    ).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });
  it("reads an external document with its hash without inventing a revision", async () => {
    const api = new WikiApi(async () =>
      envelope({
        document_id: "ext_1",
        kind: "external_document",
        content_hash: "synthetic-hash",
        text: "Synthetic external document",
        text_basis: "retained_text",
      }),
    );
    const result = await api.evidence({
      external_document_id: "ext_1",
      content_hash: "synthetic-hash",
    });
    expect(result.data.text).toBe("Synthetic external document");
    expect(result.data.revision).toBeUndefined();
  });
});
it.each([
  { entry_id: "other", revision: 2 },
  { entry_id: "requested", revision: 3 },
])(
  "rejects a formal entry response outside the exact requested binding %j",
  async (value) => {
    const api = new WikiApi(async () =>
      envelope({
        ...value,
        title: "Synthetic",
        body: "must not render",
        deleted: false,
      }),
    );
    await expect(api.entry("requested", 2)).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
  },
);
it.each([0, -1, 1.5, Number.NaN, Number.MAX_SAFE_INTEGER + 1])(
  "does not silently read latest for invalid exact revision %s",
  async (revision) => {
    const transport = vi.fn();
    await expect(
      new WikiApi(transport).entry("requested", revision),
    ).rejects.toMatchObject({ code: "INVALID_INPUT" });
    expect(transport).not.toHaveBeenCalled();
  },
);
