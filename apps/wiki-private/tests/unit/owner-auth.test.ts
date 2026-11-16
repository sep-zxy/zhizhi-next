import { afterEach, describe, expect, it, vi } from "vitest";
import { WikiApi } from "../../src/api";
import { parseHealth } from "../../src/contracts";
// Isolated synthetic protocol values; not a configured identity or live session.
const PROOF = "O".repeat(43),
  CSRF = "S".repeat(43);
const protocol = { fixture: false, authentication_mode: "owner_loopback" };
const pending = {
  ...protocol,
  status: "waiting_approval",
  pairing_code: "B".repeat(20),
  pairing_proof: PROOF,
  expires_in_seconds: 120,
};
const session = {
  ...protocol,
  status: "authenticated",
  csrf_token: CSRF,
  expires_in_seconds: 900,
  scopes: ["knowledge:read", "knowledge:write", "knowledge:review"],
};
const health = (mode: unknown) => ({
  status: "ready",
  service: "synthetic",
  worker_enabled: false,
  channel_worker_enabled: false,
  channel_status: "disabled",
  automatic_commit_enabled: false,
  publication_enabled: false,
  browser_authentication_mode: mode,
});
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });
const envelope = (data: unknown) =>
  json({ data, task_id: null, trace_id: "synthetic" });
function fixture(
  options: {
    mode?: unknown;
    pending?: unknown;
    session?: unknown;
    logoutError?: boolean;
  } = {},
) {
  const calls: { path: string; init: RequestInit }[] = [];
  const state = { mode: options.mode ?? "owner_loopback", rejected: false };
  const api = new WikiApi(async (input, init = {}) => {
    const path = String(input);
    calls.push({ path, init });
    if (path === "/health") return json(health(state.mode));
    if (state.rejected)
      return json({ error: { code: "UNAUTHENTICATED" } }, 401);
    if (path.endsWith("/request")) return json(options.pending ?? pending);
    if (path.endsWith("/session")) return json(options.session ?? session);
    if (path.endsWith("/logout")) {
      if (options.logoutError) throw Error("inert network failure");
      return json({ ...protocol, status: "waiting_auth" });
    }
    if (path.endsWith("/intakes"))
      return envelope({ intake_id: "int_owner", state: "queued" });
    if (path.endsWith("/decisions")) return envelope({ state: "recorded" });
    return envelope([]);
  });
  return { api, calls, state };
}
async function pair(api: WikiApi) {
  await api.requestBrowserPairing();
  await api.checkBrowserSession();
}
afterEach(() => vi.restoreAllMocks());
describe("independent, explicitly configured owner protocol", () => {
  it("uses only the health-selected owner prefix, keeps proof/CSRF private, and permits scoped requests", async () => {
    const { api, calls } = fixture();
    await pair(api);
    expect(calls.map((c) => c.path)).toEqual([
      "/health",
      "/api/knowledge/v1/auth/owner/request",
      "/api/knowledge/v1/auth/owner/session",
    ]);
    expect(api.getBrowserSession()).toMatchObject({
      status: "authenticated",
      mode: "owner_loopback",
      fixture: false,
      scopes: session.scopes,
    });
    expect(api.isFixtureSession()).toBe(false);
    expect(Object.isFrozen(api.getBrowserSession().scopes)).toBe(true);
    await api.submitIntake(
      {
        url: "https://example.invalid/synthetic",
        requested_action: "preview",
        data_classification: "personal",
      },
      "fixed",
    );
    const call = calls.at(-1)!;
    expect(call.init.credentials).toBe("same-origin");
    expect(new Headers(call.init.headers).get("X-Wiki-Pairing-Proof")).toBe(
      PROOF,
    );
    expect(new Headers(call.init.headers).get("X-Wiki-CSRF")).toBe(CSRF);
    expect(JSON.parse(String(call.init.body))).toMatchObject({
      data_classification: "personal",
    });
    await api.health();
    expect(
      new Headers(calls.at(-1)!.init.headers).has("X-Wiki-Pairing-Proof"),
    ).toBe(false);
    expect(
      calls.every(
        (c) =>
          !new Headers(c.init.headers).has("Authorization") &&
          !c.path.includes(PROOF) &&
          !c.path.includes(CSRF),
      ),
    ).toBe(true);
    expect(
      JSON.stringify(api) + JSON.stringify(api.getBrowserSession()),
    ).not.toContain(PROOF);
    expect(
      JSON.stringify(api) + JSON.stringify(api.getBrowserSession()),
    ).not.toContain(CSRF);
  });
  it.each(["disabled", undefined, "unrecognized"])(
    "does not request pairing for disabled/missing/invalid mode %s",
    async (mode) => {
      const { api, calls } = fixture();
      const direct = new WikiApi(async (input) => {
        calls.push({ path: String(input), init: {} });
        return json(health(mode));
      });
      await expect(direct.requestBrowserPairing()).rejects.toBeTruthy();
      expect(calls.map((c) => c.path)).toEqual(["/health"]);
      expect(api.getBrowserSession().status).toBe("waiting_auth");
    },
  );
  it.each([
    { ...pending, fixture: true },
    { ...pending, authentication_mode: "local_test" },
    { ...pending, authentication_mode: undefined },
    { ...pending, scopes: ["knowledge:read"] },
  ])(
    "rejects cross-mode or prematurely granted pending response",
    async (pending) => {
      const { api } = fixture({ pending });
      await expect(api.requestBrowserPairing()).rejects.toMatchObject({
        code: "INVALID_RESPONSE",
      });
      expect(api.getBrowserSession().status).toBe("waiting_auth");
      expect(api.isAccessBlocked()).toBe(true);
    },
  );
  it.each([
    undefined,
    [],
    ["*"],
    ["knowledge:apply"],
    ["knowledge:read", "knowledge:read"],
    ["knowledge:read", 42],
  ])("rejects invalid owner scopes %j", async (scopes) => {
    const { api } = fixture({ session: { ...session, scopes } });
    await api.requestBrowserPairing();
    await expect(api.checkBrowserSession()).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
    expect(api.isAccessBlocked()).toBe(true);
  });
  it("read-only owner cannot send writes or review, and write-only owner cannot review", async () => {
    for (const scopes of [
      ["knowledge:read"],
      ["knowledge:read", "knowledge:write"],
    ]) {
      const { api, calls } = fixture({ session: { ...session, scopes } });
      await pair(api);
      const before = calls.length;
      await expect(
        api.decideChange("change", {}, "fixed"),
      ).rejects.toMatchObject({ status: 403 });
      if (scopes.length === 1)
        await expect(
          api.controlTask("task", "cancel", 1, "fixed"),
        ).rejects.toMatchObject({ status: 403 });
      expect(calls).toHaveLength(before);
      expect(api.canPerform("knowledge:read")).toBe(true);
    }
  });
  it("clears owner memory and private access when health mode changes or becomes malformed", async () => {
    for (const mode of ["disabled", "local_test", "wrong"]) {
      const { api, state } = fixture();
      await pair(api);
      state.mode = mode;
      try {
        await api.health();
      } catch {
        /* Expected invalid health is not accepted. */
      }
      expect(api.getBrowserSession().status).toBe("waiting_auth");
      expect(api.isAccessBlocked()).toBe(true);
    }
  });
  it("clears on 401, expires before transport, and does not recover from cookie alone", async () => {
    const { api, calls, state } = fixture();
    await pair(api);
    state.rejected = true;
    await expect(api.entries()).rejects.toMatchObject({ status: 401 });
    expect(api.getBrowserSession().status).toBe("waiting_auth");
    state.rejected = false;
    await pair(api);
    const count = calls.length;
    vi.spyOn(Date, "now").mockReturnValue(
      api.getBrowserSession().expiresAt + 1,
    );
    await expect(api.entries()).rejects.toMatchObject({ status: 401 });
    expect(calls).toHaveLength(count);
    await expect(
      new WikiApi(async () => {
        throw Error("must not request");
      }).checkBrowserSession(),
    ).rejects.toMatchObject({ status: 401 });
  });
  it.each([false, true])(
    "clears memory on confirmed or uncertain owner logout (%s)",
    async (logoutError) => {
      const { api, calls } = fixture({ logoutError });
      await pair(api);
      if (logoutError)
        await expect(api.logoutBrowserSession()).rejects.toMatchObject({
          code: "NETWORK_ERROR",
        });
      else await api.logoutBrowserSession();
      expect(calls.at(-1)!.path).toBe("/api/knowledge/v1/auth/owner/logout");
      expect(api.getBrowserSession().status).toBe("waiting_auth");
      expect(api.isAccessBlocked()).toBe(true);
    },
  );
});
it("ignores a stale health response after an explicitly newer owner pairing", async () => {
  let finish!: (value: Response) => void;
  let reads = 0;
  const api = new WikiApi(async (input) => {
    const path = String(input);
    if (path === "/health") {
      reads++;
      return reads === 1
        ? new Promise<Response>((resolve) => {
            finish = resolve;
          })
        : json(health("owner_loopback"));
    }
    return json(path.endsWith("/request") ? pending : session);
  });
  const old = api.health();
  await pair(api);
  finish(json(health("invalid")));
  await expect(old).rejects.toMatchObject({ name: "AbortError" });
  expect(api.getBrowserSession().status).toBe("authenticated");
  expect(api.getBrowserMode()).toBe("owner_loopback");
});
it("stores only canonical launcher URLs and clears invalid navigation rather than retaining an old target", async () => {
  let target: unknown = "http://127.0.0.1:49152/";
  const api = new WikiApi(async () =>
    json({ ...health("owner_loopback"), learning_viewer_url: target }),
  );
  await api.health();
  expect(api.getLearningViewerUrl()).toBe(target);
  target = null;
  await api.health();
  expect(api.getLearningViewerUrl()).toBeNull();
  target = "https://untrusted.invalid/";
  await expect(api.health()).rejects.toMatchObject({
    code: "INVALID_RESPONSE",
  });
  expect(api.getLearningViewerUrl()).toBeNull();
});

it.each([
  { value: ["owner_loopback"] },
  { value: { mode: "owner_loopback" } },
  { value: null },
  { value: 123 },
  { value: "owner_loopback\n" },
])("rejects non-string or inexact health mode $value", async ({ value }) => {
  expect(() => parseHealth(health(value))).toThrow("INVALID_RESPONSE");
  const paths: string[] = [];
  const api = new WikiApi(async (input) => {
    paths.push(String(input));
    return json(String(input) === "/health" ? health(value) : pending);
  });
  await expect(api.requestBrowserPairing()).rejects.toMatchObject({
    code: "INVALID_RESPONSE",
  });
  expect(api.getBrowserSession().status).toBe("waiting_auth");
  expect(paths).toEqual(["/health"]);
});
it.each(["\n", "\r", "\r\n"])(
  "rejects line endings in pairing codes and private proofs %j",
  async (ending) => {
    for (const field of ["pairing_code", "pairing_proof"] as const) {
      const { api } = fixture({
        pending: { ...pending, [field]: pending[field] + ending },
      });
      await expect(api.requestBrowserPairing()).rejects.toMatchObject({
        code: "INVALID_RESPONSE",
      });
    }
    const { api } = fixture({
      session: { ...session, csrf_token: CSRF + ending },
    });
    await api.requestBrowserPairing();
    await expect(api.checkBrowserSession()).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
  },
);
