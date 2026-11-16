// @vitest-environment jsdom
/** Synthetic FileReader/DOM and transport checks, not live-browser acceptance. */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WikiApi, type Transport } from "../../src/api";
import { parseIntake } from "../../src/contracts";
import { TextAttachmentForm } from "../../src/text-attachment";
import { WikiApp } from "../../src/WikiApp";
const intake = parseIntake({
  intake_id: "int_file",
  state: "needs_input",
  preview_revision: 0,
  expires_at: "2099-01-01T00:00:00Z",
});
const envelope = (data: unknown) =>
  new Response(JSON.stringify({ data, task_id: null, trace_id: "synthetic" }));
const receipt = () =>
  envelope({ ...intake, state: "partial", preview_revision: 1 });
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function setup(
  handler: (index: number) => Response | Promise<Response> = receipt,
) {
  const calls: { path: string; body: unknown; key: string | null }[] = [];
  const transport: Transport = async (input, init) => {
    calls.push({
      path: String(input),
      body: JSON.parse(String(init?.body)),
      key: new Headers(init?.headers).get("Idempotency-Key"),
    });
    return handler(calls.length);
  };
  return { api: new WikiApi(transport), calls };
}
function choose(
  file = new File(["# Synthetic file\r\n"], "source.md", {
    type: "text/markdown",
  }),
) {
  fireEvent.change(screen.getByLabelText("选择文本文件"), {
    target: { files: [file] },
  });
}
async function selected() {
  await screen.findByLabelText("文件私人附言（不属于来源正文）");
}

describe("bounded actual text-file selection", () => {
  it("uploads original UTF-8/BOM/CRLF bytes only after confirmation and clears its local copy after receipt", async () => {
    const { api, calls } = setup();
    const reload = vi.fn();
    const user = userEvent.setup();
    render(<TextAttachmentForm api={api} item={intake} reload={reload} />);
    const text = "\uFEFF# 合成正文\r\n<script>not executable</script>\r\n";
    choose(new File([text], "source.md", { type: "text/markdown" }));
    await selected();
    expect(calls).toHaveLength(0);
    expect(document.querySelector("script")).toBeNull();
    expect(
      (
        screen.getByRole("button", {
          name: "上传核对后的文本文件",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    await user.type(
      screen.getByLabelText("文件私人附言（不属于来源正文）"),
      "Synthetic private note",
    );
    await user.click(screen.getByRole("checkbox"));
    await user.click(
      screen.getByRole("button", { name: "上传核对后的文本文件" }),
    );
    await screen.findByText("文本文件已进入临时预览，尚未保留");
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe(
      "/api/knowledge/v1/intakes/int_file/text-attachment",
    );
    expect(calls[0].body).toEqual({
      expected_preview_revision: 0,
      filename: "source.md",
      content_type: "text/markdown",
      content_base64: Buffer.from(text, "utf8").toString("base64"),
      private_note: "Synthetic private note",
    });
    expect(calls[0].key).toBeTruthy();
    expect(
      screen.queryByLabelText("文件私人附言（不属于来源正文）"),
    ).toBeNull();
    expect(document.body.textContent).not.toContain("not executable");
    expect(reload).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "查看上传后的新预览" }),
    );
    expect(reload).toHaveBeenCalledOnce();
  });
  it("cancel clears source, note, file input and confirmation without sending", async () => {
    const { api, calls } = setup();
    const user = userEvent.setup();
    render(<TextAttachmentForm api={api} item={intake} reload={() => {}} />);
    choose();
    await selected();
    await user.type(
      screen.getByLabelText("文件私人附言（不属于来源正文）"),
      "private draft",
    );
    await user.click(screen.getByRole("checkbox"));
    await user.click(
      screen.getByRole("button", { name: "取消并清除所选文件" }),
    );
    expect(calls).toHaveLength(0);
    expect(document.body.textContent).not.toContain("Synthetic file");
    expect(document.body.textContent).not.toContain("private draft");
    expect(
      (screen.getByLabelText("选择文本文件") as HTMLInputElement).value,
    ).toBe("");
    choose(new File(["New synthetic text"], "new.txt"));
    await selected();
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(
      false,
    );
    expect(
      (
        screen.getByLabelText(
          "文件私人附言（不属于来源正文）",
        ) as HTMLTextAreaElement
      ).value,
    ).toBe("");
  });
  it.each([
    ["large.txt", "text/plain", "x".repeat(100001)],
    ["archive.zip", "application/zip", "not uploaded"],
    ["source.txt", "application/octet-stream", "not uploaded"],
    ["bad.txt", "text/plain", new Uint8Array([255])],
    ["binary.txt", "text/plain", new Uint8Array([0, 1])],
  ])(
    "rejects invalid size/type/UTF-8 before transport: %s",
    async (name, type, body) => {
      const { api, calls } = setup();
      render(<TextAttachmentForm api={api} item={intake} reload={() => {}} />);
      choose(new File([body], name, { type }));
      await screen.findByRole("alert");
      expect(calls).toHaveLength(0);
      expect(screen.queryByRole("checkbox")).toBeNull();
    },
  );
  it("checks expiry again at the click and removes selected bytes", async () => {
    const { api, calls } = setup();
    const user = userEvent.setup();
    const now = Date.now();
    render(
      <TextAttachmentForm
        api={api}
        item={{ ...intake, expires_at: new Date(now + 60000).toISOString() }}
        reload={() => {}}
      />,
    );
    choose();
    await selected();
    await user.click(screen.getByRole("checkbox"));
    vi.spyOn(Date, "now").mockReturnValue(now + 60001);
    fireEvent.click(
      screen.getByRole("button", { name: "上传核对后的文本文件" }),
    );
    expect(calls).toHaveLength(0);
    expect(screen.getByRole("alert").textContent).toContain("临时预览已到期");
    expect(document.body.textContent).not.toContain("Synthetic file");
  });
  it("freezes uncertain uploads and retries identical bytes, note, revision and key", async () => {
    const { api, calls } = setup((index) => {
      if (index === 1) throw new Error("uncertain synthetic failure");
      return receipt();
    });
    const user = userEvent.setup();
    render(<TextAttachmentForm api={api} item={intake} reload={() => {}} />);
    choose();
    await selected();
    await user.click(screen.getByRole("checkbox"));
    await user.click(
      screen.getByRole("button", { name: "上传核对后的文本文件" }),
    );
    await screen.findByRole("button", { name: "重试同一请求" });
    expect(
      (screen.getByLabelText("选择文本文件") as HTMLInputElement).disabled,
    ).toBe(true);
    expect(
      screen.queryByRole("button", { name: "取消并清除所选文件" }),
    ).toBeNull();
    await user.click(screen.getByRole("button", { name: "重试同一请求" }));
    await screen.findByText("文本文件已进入临时预览，尚未保留");
    expect(calls[0]).toEqual(calls[1]);
  });
  it("rejects local-test upload both in the UI and transport before any file write request", async () => {
    const requests: string[] = [];
    const api = new WikiApi(async (input) => {
      requests.push(String(input));
      if (String(input) === "/health")
        return new Response(
          JSON.stringify({
            status: "ready",
            service: "fixture",
            worker_enabled: false,
            channel_worker_enabled: false,
            channel_status: "disabled",
            automatic_commit_enabled: false,
            publication_enabled: false,
            browser_authentication_mode: "local_test",
          }),
        );
      return new Response(
        JSON.stringify(
          String(input).endsWith("/request")
            ? {
                status: "waiting_approval",
                fixture: true,
                pairing_code: "A".repeat(20),
                pairing_proof: "P".repeat(43),
                expires_in_seconds: 120,
              }
            : {
                status: "authenticated",
                fixture: true,
                csrf_token: "C".repeat(43),
                expires_in_seconds: 900,
              },
        ),
      );
    });
    await api.requestBrowserPairing();
    await api.checkBrowserSession();
    render(<TextAttachmentForm api={api} item={intake} reload={() => {}} />);
    expect(
      (screen.getByLabelText("选择文本文件") as HTMLInputElement).disabled,
    ).toBe(true);
    await expect(
      api.uploadTextAttachment(
        intake.intake_id,
        {
          expected_preview_revision: 0,
          filename: "source.txt",
          content_type: "text/plain",
          content_base64: "eA==",
          private_note: "",
        },
        "same-key",
      ),
    ).rejects.toMatchObject({ code: "LOCAL_TEST_WRITES_DISABLED" });
    expect(requests).toHaveLength(3);
  });
  it("ignores an older FileReader completion and aborts on unmount", async () => {
    const readers: FileReader[] = [];
    vi.spyOn(FileReader.prototype, "readAsArrayBuffer").mockImplementation(
      function (this: FileReader) {
        readers.push(this);
      },
    );
    const abort = vi.spyOn(FileReader.prototype, "abort");
    const { api, calls } = setup();
    const { unmount } = render(
      <TextAttachmentForm api={api} item={intake} reload={() => {}} />,
    );
    const old = new File(["old"], "old.txt"),
      newer = new File(["new"], "new.txt");
    choose(old);
    choose(newer);
    function complete(reader: FileReader, value: string) {
      Object.defineProperty(reader, "result", {
        value: new TextEncoder().encode(value).buffer,
      });
      reader.onload?.call(
        reader,
        new ProgressEvent("load") as ProgressEvent<FileReader>,
      );
    }
    act(() => complete(readers[1], "new"));
    await selected();
    act(() => complete(readers[0], "old"));
    expect(document.body.textContent).toContain("new.txt");
    expect(document.body.textContent).not.toContain("old.txt");
    choose(old);
    unmount();
    act(() => complete(readers[2], "old"));
    expect(abort).toHaveBeenCalled();
    expect(calls).toHaveLength(0);
    expect(document.body.textContent).not.toContain("old");
  });
  it("clears selected source and note when an authenticated API becomes unauthorized", async () => {
    const api = new WikiApi(async (input) =>
      String(input).includes("/intakes/") ? envelope(intake) : envelope([]),
    );
    window.location.hash = "#/intake/int_file";
    render(<WikiApp api={api} />);
    await screen.findByLabelText("选择文本文件");
    choose();
    await selected();
    fireEvent.change(screen.getByLabelText("文件私人附言（不属于来源正文）"), {
      target: { value: "synthetic private draft" },
    });
    act(() => api.forgetBrowserSession());
    await waitFor(() =>
      expect(screen.queryByLabelText("选择文本文件")).toBeNull(),
    );
    expect(document.body.textContent).not.toContain("Synthetic file");
    expect(document.body.textContent).not.toContain("synthetic private draft");
  });
});
