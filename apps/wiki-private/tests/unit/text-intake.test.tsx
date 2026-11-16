// @vitest-environment jsdom
/** Synthetic transport/DOM only, not live browser or Windows acceptance. */
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  WikiApi,
  intakeSubmissionError,
  type IntakeSubmission,
} from "../../src/api";
import { InboxPage } from "../../src/workflow-pages";
import { WikiApp } from "../../src/WikiApp";

const original = "  合成原话 😀\n第二行\n  ";
const body = (text: string): IntakeSubmission => ({
  text,
  requested_action: "preview",
  data_classification: "personal",
});
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify({ data, trace_id: "synthetic", task_id: null }), {
    status,
  });
const receipt = {
  intake_id: "int_text",
  state: "received",
  preview_revision: 0,
};
function fixture(
  reply: (index: number) => Promise<Response> | Response = () => json(receipt),
) {
  const writes: {
    body: unknown;
    key: string | null;
    credentials: RequestCredentials | undefined;
  }[] = [];
  const api = new WikiApi(async (input, init) => {
    const path = String(input);
    if (path === "/health")
      return new Response(
        JSON.stringify({
          status: "ready",
          service: "synthetic",
          worker_enabled: false,
          channel_worker_enabled: false,
          channel_status: "disabled",
          automatic_commit_enabled: false,
          publication_enabled: false,
          browser_authentication_mode: "disabled",
          learning_viewer_url: null,
        }),
      );
    if (path === "/api/knowledge/v1/intakes" && init?.method === "POST") {
      writes.push({
        body: JSON.parse(String(init.body)),
        key: new Headers(init.headers).get("Idempotency-Key"),
        credentials: init.credentials,
      });
      return reply(writes.length);
    }
    if (
      path.startsWith("/api/knowledge/v1/intakes?") ||
      path.startsWith("/api/knowledge/v1/tasks?")
    )
      return json({ items: [], has_more: false });
    if (path.startsWith("/api/knowledge/v1/entries?")) return json([]);
    if (path === "/api/knowledge/v1/intakes/int_text") return json(receipt);
    throw Error("Unexpected synthetic route: " + path);
  });
  return { api, writes };
}
beforeEach(() => window.history.replaceState({}, "", "/wiki/#/inbox"));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
async function openText(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "投递材料" }));
  await user.selectOptions(screen.getByLabelText("材料类型"), "text");
}
describe("first intake text API contract", () => {
  it("preserves exact Unicode, CRLF and surrounding whitespace without adding URL or authority", async () => {
    const f = fixture(),
      text = "  合成 😀\r\n正文\n  ";
    await f.api.submitIntake(body(text), "same-text-key");
    expect(f.writes).toEqual([
      { body: body(text), key: "same-text-key", credentials: "same-origin" },
    ]);
  });
  it.each([
    { text: " \n\t ", code: "EMPTY_TEXT" },
    { text: "a".repeat(100001), code: "TEXT_TOO_LARGE" },
    { text: "\u0001".repeat(100000), code: "BODY_TOO_LARGE" },
  ])("rejects $code before transport", async ({ text, code }) => {
    const f = fixture();
    await expect(
      f.api.submitIntake(body(text), "unused"),
    ).rejects.toMatchObject({ code });
    expect(f.writes).toHaveLength(0);
  });
  it("counts Unicode code points rather than UTF-16 units", () => {
    expect(intakeSubmissionError(body("😀".repeat(100000)))).toBeNull();
    expect(intakeSubmissionError(body("😀".repeat(100001)))).toBe(
      "TEXT_TOO_LARGE",
    );
  });
  it("keeps all text intake disabled for local-test sessions", async () => {
    const f = fixture();
    vi.spyOn(f.api, "isFixtureSession").mockReturnValue(true);
    await expect(
      f.api.submitIntake(body(original), "unused"),
    ).rejects.toMatchObject({ status: 403 });
    expect(f.writes).toHaveLength(0);
  });
});
describe("first intake form", () => {
  it("freezes exact text and idempotency after failure, only explicitly retries, then clears draft", async () => {
    const f = fixture((index) =>
      index === 1 ? Promise.reject(Error("synthetic offline")) : json(receipt),
    );
    const user = userEvent.setup();
    render(
      <StrictMode>
        <InboxPage api={f.api} />
      </StrictMode>,
    );
    await openText(user);
    fireEvent.change(screen.getByLabelText("来源正文"), {
      target: { value: original },
    });
    await user.click(screen.getByRole("button", { name: "提交临时预览" }));
    await screen.findByRole("button", { name: "重试同一请求" });
    expect(f.writes).toHaveLength(1);
    expect(f.writes[0].body).toEqual(body(original));
    expect(
      (screen.getByLabelText("来源正文") as HTMLTextAreaElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByLabelText("材料类型") as HTMLSelectElement).disabled,
    ).toBe(true);
    await user.click(screen.getByRole("button", { name: "重试同一请求" }));
    await waitFor(() => expect(window.location.hash).toBe("#/intake/int_text"));
    expect(f.writes).toHaveLength(2);
    expect(f.writes[1]).toEqual(f.writes[0]);
    expect(
      (screen.getByLabelText("来源正文") as HTMLTextAreaElement).value,
    ).toBe("");
  });
  it("clears material on mode changes and cancel, and preserves the original URL path", async () => {
    const f = fixture(),
      user = userEvent.setup();
    render(<InboxPage api={f.api} />);
    await openText(user);
    fireEvent.change(screen.getByLabelText("来源正文"), {
      target: { value: original },
    });
    await user.selectOptions(screen.getByLabelText("材料类型"), "url");
    expect((screen.getByLabelText("来源链接") as HTMLInputElement).value).toBe(
      "",
    );
    await user.selectOptions(screen.getByLabelText("材料类型"), "text");
    expect(
      (screen.getByLabelText("来源正文") as HTMLTextAreaElement).value,
    ).toBe("");
    fireEvent.change(screen.getByLabelText("来源正文"), {
      target: { value: original },
    });
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(f.writes).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "投递材料" }));
    await user.type(
      screen.getByLabelText("来源链接"),
      "https://example.com/synthetic",
    );
    await user.click(screen.getByRole("button", { name: "提交临时预览" }));
    expect(f.writes[0].body).toEqual({
      url: "https://example.com/synthetic",
      requested_action: "preview",
      data_classification: "personal",
    });
  });
  it("rejects empty or oversized text without POST", async () => {
    const f = fixture(),
      user = userEvent.setup();
    render(<InboxPage api={f.api} />);
    await openText(user);
    fireEvent.change(screen.getByLabelText("来源正文"), {
      target: { value: " \n " },
    });
    expect(
      (
        screen.getByRole("button", {
          name: "提交临时预览",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    fireEvent.change(screen.getByLabelText("来源正文"), {
      target: { value: "a".repeat(100001) },
    });
    await user.click(screen.getByRole("button", { name: "提交临时预览" }));
    await screen.findByText("来源正文最多 100,000 个 Unicode 字符。");
    expect(f.writes).toHaveLength(0);
  });
  it("ignores a receipt after cancelling and reopening a newer draft", async () => {
    let finish!: (response: Response) => void;
    const f = fixture(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      ),
      user = userEvent.setup();
    render(
      <StrictMode>
        <InboxPage api={f.api} />
      </StrictMode>,
    );
    await openText(user);
    fireEvent.change(screen.getByLabelText("来源正文"), {
      target: { value: original },
    });
    await user.click(screen.getByRole("button", { name: "提交临时预览" }));
    await user.click(screen.getByRole("button", { name: "取消" }));
    await openText(user);
    fireEvent.change(screen.getByLabelText("来源正文"), {
      target: { value: "New synthetic draft" },
    });
    await act(async () => finish(json(receipt)));
    expect(window.location.hash).toBe("#/inbox");
    expect(
      (screen.getByLabelText("来源正文") as HTMLTextAreaElement).value,
    ).toBe("New synthetic draft");
    expect(f.writes).toHaveLength(1);
  });
  it("clears private text on 401 and does not retry", async () => {
    const f = fixture(
        () =>
          new Response(JSON.stringify({ error: { code: "UNAUTHENTICATED" } }), {
            status: 401,
          }),
      ),
      user = userEvent.setup();
    render(<WikiApp api={f.api} />);
    await openText(user);
    fireEvent.change(screen.getByLabelText("来源正文"), {
      target: { value: original },
    });
    await user.click(screen.getByRole("button", { name: "提交临时预览" }));
    await screen.findByRole("heading", { name: "连接私有工作区" });
    expect(screen.queryByLabelText("来源正文")).toBeNull();
    expect(document.body.textContent).not.toContain(original);
    expect(f.writes).toHaveLength(1);
  });
  it.each(["read-only", "local-test"])(
    "blocks material form in %s mode",
    async (mode) => {
      const f = fixture();
      if (mode === "local-test")
        vi.spyOn(f.api, "isFixtureSession").mockReturnValue(true);
      else vi.spyOn(f.api, "canPerform").mockReturnValue(false);
      render(<InboxPage api={f.api} />);
      expect(
        (screen.getByRole("button", { name: "投递材料" }) as HTMLButtonElement)
          .disabled,
      ).toBe(true);
      expect(screen.queryByLabelText("来源正文")).toBeNull();
      expect(f.writes).toHaveLength(0);
    },
  );
});
