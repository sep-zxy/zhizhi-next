import { expect, type Page, type Route } from "@playwright/test";
/** Synthetic intercepted API only. Never use real identities/data with trace-enabled tests. */
export const entry = {
  entry_id: "entry_synthetic.1",
  revision: 3,
  title: "Synthetic formal entry",
  body: "Synthetic formal source body",
  deleted: false,
  entry_type: "concept",
  visibility: "private",
  publication_intent: "public",
  evidence_refs: [
    {
      source_id: "src_synthetic",
      source_revision: 1,
      content_hash: "synthetic-hash",
    },
  ],
};
export const intake = {
  intake_id: "int_synthetic",
  state: "partial",
  preview_revision: 2,
  title: "Synthetic intake",
  channel: "http",
  expires_at: "2099-01-01T00:00:00Z",
  preview: {
    document: {
      title: "Synthetic source",
      body_text: "Original synthetic source",
      body_complete: false,
      metadata: { text_basis: "user_supplied" },
    },
    summary_state: "not_requested",
  },
};
export const task = {
  task_id: "tsk_synthetic",
  kind: "knowledge.generate",
  state: "waiting_auth",
  revision: 4,
  attempt: 1,
  max_attempts: 5,
};
export const PROOF = "P".repeat(43),
  CSRF = "C".repeat(43),
  CODE = "A".repeat(20);
export type Write = {
  path: string;
  body: Record<string, unknown>;
  key: string | undefined;
  headers: Record<string, string>;
};
export const reply = (route: Route, data: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify({ data, task_id: null, trace_id: "synthetic" }),
  });
export const reject = (route: Route, code: string, status = 409) =>
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify({ error: { code }, trace_id: "synthetic" }),
  });
export async function setupV1(
  page: Page,
  options: {
    scopes?: string[];
    expiresAt?: string;
    taskState?: string;
    sessionTtl?: number;
    mismatch?: boolean;
    onWrite?: (route: Route, write: Write, index: number) => Promise<unknown>;
    logoutFailure?: boolean;
  } = {},
) {
  const writes: Write[] = [],
    reads: string[] = [],
    authPaths: string[] = [];
  let approved = false,
    authenticated = false;
  const flags = { fixture: false, authentication_mode: "owner_loopback" };
  await page.route(
    (url) =>
      url.pathname === "/health" ||
      url.pathname.startsWith("/api/knowledge/v1/"),
    async (route) => {
      const request = route.request(),
        url = new URL(request.url()),
        path = url.pathname,
        headers = request.headers();
      expect(headers.authorization).toBeUndefined();
      const authReply = (value: unknown) =>
        route.fulfill({
          contentType: "application/json",
          body: JSON.stringify(value),
        });
      if (path === "/health") {
        expect(headers["x-wiki-pairing-proof"]).toBeUndefined();
        expect(headers["x-wiki-csrf"]).toBeUndefined();
        return authReply({
          status: "ready",
          service: "synthetic",
          worker_enabled: false,
          channel_worker_enabled: false,
          channel_status: "disabled",
          automatic_commit_enabled: false,
          publication_enabled: false,
          browser_authentication_mode: "owner_loopback",
          learning_viewer_url: "http://127.0.0.1:49152/",
        });
      }
      if (path.includes("/auth/")) {
        authPaths.push(path);
        expect(path.startsWith("/api/knowledge/v1/auth/owner/")).toBe(true);
        if (path.endsWith("/request")) {
          expect(request.method()).toBe("POST");
          expect(request.postDataJSON()).toEqual({});
          expect(headers["x-wiki-pairing-proof"]).toBeUndefined();
          return authReply({
            ...flags,
            fixture: options.mismatch ? true : false,
            status: "waiting_approval",
            pairing_code: CODE,
            pairing_proof: PROOF,
            expires_in_seconds: 120,
          });
        }
        expect(headers["x-wiki-pairing-proof"]).toBe(PROOF);
        if (path.endsWith("/session")) {
          expect(request.method()).toBe("GET");
          if (!approved)
            return authReply({ ...flags, status: "waiting_approval" });
          authenticated = true;
          return authReply({
            ...flags,
            status: "authenticated",
            csrf_token: CSRF,
            expires_in_seconds: options.sessionTtl ?? 900,
            scopes: options.scopes ?? [
              "knowledge:read",
              "knowledge:write",
              "knowledge:review",
            ],
          });
        }
        if (path.endsWith("/logout")) {
          expect(request.method()).toBe("POST");
          expect(request.postDataJSON()).toEqual({});
          expect(headers["x-wiki-csrf"]).toBe(CSRF);
          if (options.logoutFailure) return route.abort("connectionfailed");
          authenticated = false;
          return authReply({ ...flags, status: "waiting_auth" });
        }
        throw Error("Unexpected synthetic auth route");
      }
      if (!authenticated) return reject(route, "UNAUTHENTICATED", 401);
      expect(headers["x-wiki-pairing-proof"]).toBe(PROOF);
      const short = path.slice("/api/knowledge/v1".length);
      if (request.method() === "POST") {
        expect(headers["x-wiki-csrf"]).toBe(CSRF);
        const write = {
          path: short,
          body: request.postDataJSON(),
          key: headers["idempotency-key"],
          headers,
        };
        if (short !== "/evidence/read") expect(write.key).toBeTruthy();
        else expect(write.key).toBeUndefined();
        writes.push(write);
        if (options.onWrite)
          return options.onWrite(route, write, writes.length);
        throw Error("Unexpected synthetic write: " + short);
      }
      expect(request.method()).toBe("GET");
      expect(headers["x-wiki-csrf"]).toBeUndefined();
      reads.push(short);
      if (short === "/entries") return reply(route, [entry]);
      if (short === "/entries/" + entry.entry_id)
        return reply(route, {
          ...entry,
          revision: Number(url.searchParams.get("revision")) || entry.revision,
        });
      if (short === "/intakes")
        return reply(route, { items: [intake], has_more: false });
      if (short === "/intakes/" + intake.intake_id)
        return reply(route, {
          ...intake,
          expires_at: options.expiresAt ?? intake.expires_at,
        });
      if (short === "/tasks")
        return reply(route, { items: [task], has_more: false });
      if (short === "/tasks/" + task.task_id)
        return reply(route, {
          ...task,
          state: options.taskState ?? task.state,
        });
      if (short === "/tasks/" + task.task_id + "/events")
        return reply(route, []);
      if (short === "/reviews") return reply(route, []);
      if (short === "/intakes/capabilities")
        return reply(route, { capabilities: [] });
      throw Error("Unexpected synthetic API: " + short);
    },
  );
  return {
    writes,
    reads,
    authPaths,
    approve: () => {
      approved = true;
    },
  };
}
export async function openOwner(
  page: Page,
  fixture: Awaited<ReturnType<typeof setupV1>>,
  hash: string,
) {
  await page.goto(hash);
  await page.getByRole("button", { name: "申请 Owner 配对码" }).click();
  await expect(page.getByLabel("一次性配对码")).toHaveText(CODE);
  fixture.approve();
  await page.getByRole("button", { name: "检查本机批准" }).click();
  await expect(
    page.getByText("已连接 Owner 私有会话", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("body")).not.toContainText(PROOF);
  await expect(page.locator("body")).not.toContainText(CSRF);
}
