import { afterEach, describe, expect, it, vi } from "vitest";
import { WikiApi, type Transport } from "../../src/api";

// Inert synthetic values, never actual service credentials.
const CODE = "A1B2C3D4E5F6A7B8C9D0";
const PROOF = "P".repeat(43);
const CSRF = "C".repeat(43);
const pending = {
  status: "waiting_approval",
  pairing_code: CODE,
  pairing_proof: PROOF,
  expires_in_seconds: 120,
  fixture: true,
};
const authenticated = {
  status: "authenticated",
  csrf_token: CSRF,
  expires_in_seconds: 900,
  fixture: true,
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });
const envelope = (data: unknown) =>
  json({ data, trace_id: "fixture", task_id: null });
type Call = { path: string; init: RequestInit };
function transportFixture(
  options: { session?: unknown; logoutFailure?: boolean } = {},
) {
  const calls: Call[] = [];
  const transport: Transport = async (input, init = {}) => {
    const path = String(input);
    calls.push({ path, init });
    if (path.endsWith("/request")) return json(pending);
    if (path.endsWith("/session"))
      return json(options.session ?? authenticated);
    if (path.endsWith("/logout")) {
      if (options.logoutFailure) throw new Error("raw error must stay hidden");
      return json({ status: "waiting_auth", fixture: true });
    }
    if (path === "/health")
      return json({
        status: "ready",
        service: "fixture",
        worker_enabled: false,
        channel_worker_enabled: false,
        channel_status: "disabled",
        automatic_commit_enabled: false,
        publication_enabled: false,
      });
    return envelope(init.method === "POST" ? { state: "discarded" } : []);
  };
  return { api: new WikiApi(transport), calls, transport };
}
async function pair(api: WikiApi) {
  await api.requestLocalTestPairing();
  await api.checkLocalTestSession();
}
afterEach(() => vi.restoreAllMocks());

describe("explicit memory-only local test pairing", () => {
  it("cannot reopen domain reads while requesting or waiting for approval", async () => {
    let finish!: (response: Response) => void;
    const api = new WikiApi(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    const request = api.requestLocalTestPairing();
    expect(api.canRetryAccess()).toBe(false);
    api.retryAccess();
    expect(api.isAccessBlocked()).toBe(true);
    finish(json(pending));
    await request;
    api.retryAccess();
    expect(api.isAccessBlocked()).toBe(true);
    api.forgetLocalTestSession();
    expect(api.canRetryAccess()).toBe(true);
  });
  it("keeps the private boundary closed until uncertain logout has finished clearing memory", async () => {
    let fail!: (error: Error) => void;
    const fixture = transportFixture();
    const api = new WikiApi((input, init) =>
      String(input).endsWith("/logout")
        ? new Promise<Response>((_, reject) => {
            fail = reject;
          })
        : fixture.transport(input, init),
    );
    await pair(api);
    const logout = api.logoutLocalTestSession();
    expect(api.canRetryAccess()).toBe(false);
    api.retryAccess();
    expect(api.isAccessBlocked()).toBe(true);
    fail(new Error("inert failure"));
    await expect(logout).rejects.toMatchObject({ code: "NETWORK_ERROR" });
    expect(api.getLocalTestSession().status).toBe("waiting_auth");
    expect(api.canRetryAccess()).toBe(true);
    expect(api.isAccessBlocked()).toBe(true);
  });
  it.each([
    "CSRF_REJECTED",
    "BROWSER_ORIGIN_REJECTED",
    "BROWSER_REQUEST_REJECTED",
  ])("clears pairing memory after %s without automatic retry", async (code) => {
    const fixture = transportFixture();
    let denied = false;
    const api = new WikiApi((input, init) =>
      denied
        ? Promise.resolve(json({ error: { code } }, 403))
        : fixture.transport(input, init),
    );
    await pair(api);
    denied = true;
    await expect(api.entries()).rejects.toMatchObject({ code, status: 403 });
    expect(api.getLocalTestSession().status).toBe("waiting_auth");
    expect(api.isAccessBlocked()).toBe(true);
    denied = false;
    await api.entries();
    expect(
      new Headers(fixture.calls.at(-1)!.init.headers).has(
        "X-Wiki-Pairing-Proof",
      ),
    ).toBe(false);
  });
  it("returns only the public code snapshot and never grants access merely for requesting", async () => {
    const { api, calls } = transportFixture();
    const result = await api.requestLocalTestPairing();
    expect(result).toMatchObject({
      status: "waiting_approval",
      pairingCode: CODE,
    });
    expect(Object.isFrozen(result)).toBe(true);
    expect(api.isAccessBlocked()).toBe(true);
    expect(JSON.stringify(result) + JSON.stringify(api)).not.toContain(PROOF);
    expect(calls[0].path).toBe("/api/knowledge/v1/auth/local-test/request");
    expect(calls[0].init).toMatchObject({
      method: "POST",
      body: "{}",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
    });
    expect(new Headers(calls[0].init.headers).has("Authorization")).toBe(false);
  });
  it("keeps waiting_approval distinct from authenticated and does not poll automatically", async () => {
    const { api, calls } = transportFixture({
      session: { status: "waiting_approval", fixture: true },
    });
    await api.requestLocalTestPairing();
    await api.checkLocalTestSession();
    expect(api.getLocalTestSession().status).toBe("waiting_approval");
    expect(api.isAccessBlocked()).toBe(true);
    expect(calls).toHaveLength(2);
  });
  it("sends proof on domain reads and proof plus CSRF on writes, never bearer or health headers", async () => {
    const { api, calls } = transportFixture();
    await pair(api);
    expect(api.isAccessBlocked()).toBe(false);
    expect(api.getLocalTestSession()).not.toHaveProperty("pairingCode");
    await api.entries();
    await api.decideIntake(
      "int_synthetic",
      { decision: "discard", preview_revision: 1 },
      "same-key",
    );
    await api.health();
    const reads = new Headers(calls[2].init.headers),
      writes = new Headers(calls[3].init.headers),
      health = new Headers(calls[4].init.headers);
    expect(reads.get("X-Wiki-Pairing-Proof")).toBe(PROOF);
    expect(reads.has("X-Wiki-CSRF")).toBe(false);
    expect(writes.get("X-Wiki-Pairing-Proof")).toBe(PROOF);
    expect(writes.get("X-Wiki-CSRF")).toBe(CSRF);
    expect(writes.get("Idempotency-Key")).toBe("same-key");
    expect(health.has("X-Wiki-Pairing-Proof")).toBe(false);
    expect(health.has("X-Wiki-CSRF")).toBe(false);
    expect(
      calls.every(
        (call) => !new Headers(call.init.headers).has("Authorization"),
      ),
    ).toBe(true);
    expect(
      calls.every(
        (call) =>
          !call.path.includes(PROOF) &&
          !call.path.includes(CSRF) &&
          !call.path.includes(CODE),
      ),
    ).toBe(true);
    expect(
      JSON.stringify(api.getLocalTestSession()) + JSON.stringify(api),
    ).not.toContain(CSRF);
  });
  it("cannot recover a session from cookies after a fresh instance or refresh", async () => {
    const transport = vi.fn().mockResolvedValue(json(authenticated));
    const api = new WikiApi(transport);
    await expect(api.checkLocalTestSession()).rejects.toMatchObject({
      status: 401,
    });
    expect(transport).not.toHaveBeenCalled();
  });
  it.each([
    { ...pending, fixture: false },
    { ...pending, pairing_proof: "bad" },
    { ...pending, pairing_code: "<script>" },
    { ...pending, expires_in_seconds: 121 },
    { ...pending, expires_in_seconds: 0 },
    { ...pending, status: "authenticated" },
  ])("fails closed for malformed pairing responses", async (value) => {
    const api = new WikiApi(async () => json(value));
    await expect(api.requestLocalTestPairing()).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
    expect(api.getLocalTestSession().status).toBe("waiting_auth");
    expect(api.isAccessBlocked()).toBe(true);
  });
  it("does not accept an authenticated response with invalid CSRF or excessive expiry", async () => {
    for (const session of [
      { ...authenticated, csrf_token: "bad" },
      { ...authenticated, expires_in_seconds: 901 },
    ]) {
      const { api } = transportFixture({ session });
      await api.requestLocalTestPairing();
      await expect(api.checkLocalTestSession()).rejects.toMatchObject({
        code: "INVALID_RESPONSE",
      });
      expect(api.getLocalTestSession().status).toBe("waiting_auth");
      expect(api.isAccessBlocked()).toBe(true);
    }
  });
  it("clears proof and CSRF on 401 rather than retrying or reusing the failed session", async () => {
    const fixture = transportFixture();
    let reject = false;
    const api = new WikiApi((input, init) =>
      reject
        ? Promise.resolve(json({ error: { code: "UNAUTHENTICATED" } }, 401))
        : fixture.transport(input, init),
    );
    await pair(api);
    reject = true;
    await expect(api.entries()).rejects.toMatchObject({ status: 401 });
    expect(api.getLocalTestSession().status).toBe("waiting_auth");
    expect(api.isAccessBlocked()).toBe(true);
    reject = false;
    await api.entries();
    expect(
      new Headers(fixture.calls.at(-1)!.init.headers).has(
        "X-Wiki-Pairing-Proof",
      ),
    ).toBe(false);
  });
  it("expires locally before another request and does not silently renew", async () => {
    let now = 1000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const { api, calls } = transportFixture();
    await pair(api);
    now += 900_001;
    await expect(api.entries()).rejects.toMatchObject({ status: 401 });
    expect(calls).toHaveLength(2);
    expect(api.getLocalTestSession().status).toBe("waiting_auth");
    expect(api.isAccessBlocked()).toBe(true);
  });
  it("uses both memory headers for logout and clears the local session after confirmed logout", async () => {
    const { api, calls } = transportFixture();
    await pair(api);
    await api.logoutLocalTestSession();
    const headers = new Headers(calls.at(-1)!.init.headers);
    expect(headers.get("X-Wiki-Pairing-Proof")).toBe(PROOF);
    expect(headers.get("X-Wiki-CSRF")).toBe(CSRF);
    expect(api.isAccessBlocked()).toBe(true);
    expect(api.getLocalTestSession().status).toBe("waiting_auth");
  });
  it("clears client memory but rejects uncertain logout instead of claiming server revocation", async () => {
    const { api, calls } = transportFixture({ logoutFailure: true });
    await pair(api);
    await expect(api.logoutLocalTestSession()).rejects.toMatchObject({
      code: "NETWORK_ERROR",
    });
    expect(calls).toHaveLength(3);
    expect(api.getLocalTestSession().status).toBe("waiting_auth");
    expect(api.isAccessBlocked()).toBe(true);
  });
  it("discards an old 401 response after a new pairing request has begun", async () => {
    let finish!: (response: Response) => void;
    const api = new WikiApi(async (input) =>
      String(input).endsWith("/request")
        ? json(pending)
        : new Promise<Response>((resolve) => {
            finish = resolve;
          }),
    );
    const old = api.entries();
    await api.requestLocalTestPairing();
    finish(json({}, 401));
    await expect(old).rejects.toMatchObject({ name: "AbortError" });
    expect(api.getLocalTestSession().status).toBe("waiting_approval");
  });
  it("discards old private data when JSON decoding finishes after the session changes", async () => {
    let finish!: (body: unknown) => void;
    let decoding!: () => void;
    const started = new Promise<void>((resolve) => {
      decoding = resolve;
    });
    const slow = envelope([]);
    vi.spyOn(slow, "json").mockImplementation(() => {
      decoding();
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    const api = new WikiApi(async (input) =>
      String(input).endsWith("/request") ? json(pending) : slow,
    );
    const old = api.entries();
    await started;
    await api.requestLocalTestPairing();
    finish({ data: [], trace_id: "old", task_id: null });
    await expect(old).rejects.toMatchObject({ name: "AbortError" });
    expect(api.getLocalTestSession().status).toBe("waiting_approval");
  });
  it("does not transmit a real-link intake in the synthetic fixture session", async () => {
    const { api, calls } = transportFixture();
    await pair(api);
    await expect(
      api.submitIntake(
        {
          url: "https://example.com",
          requested_action: "preview",
          data_classification: "personal",
        },
        "not-sent",
      ),
    ).rejects.toMatchObject({ code: "LOCAL_TEST_LINKS_DISABLED" });
    expect(calls).toHaveLength(2);
  });
});
