// @vitest-environment jsdom
/** Synthetic component/transport tests, not real-browser or live-account acceptance. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
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
import {
  EntryProposal,
  EvidenceReader,
  SupplementForm,
  SupplementHistory,
  TaskControls,
} from "../../src/material-actions";
import { parseEntry, parseIntake, parseTask } from "../../src/contracts";
import { WikiApp } from "../../src/WikiApp";

const entry = parseEntry({
  entry_id: "ent_synthetic",
  revision: 3,
  title: "合成正式条目",
  body: "正式基础正文",
  deleted: false,
  entry_type: "concept",
  visibility: "private",
  publication_intent: "public",
  evidence_refs: [
    {
      source_id: "src_synthetic",
      source_revision: 1,
      content_hash: "synthetic-hash",
      quote: "引用片段",
      locator: "line 1",
    },
  ],
});
const task = parseTask({
  task_id: "tsk_synthetic",
  revision: 2,
  kind: "synthetic.task",
  state: "waiting_auth",
});
const intake = parseIntake({
  intake_id: "int_synthetic",
  state: "needs_input",
  preview_revision: 0,
  expires_at: "2099-01-01T00:00:00Z",
  channel: "http",
});
const envelope = (data: unknown) =>
  new Response(
    JSON.stringify({ data, task_id: null, trace_id: "synthetic-trace" }),
  );
const failure = (code: string, status: number) =>
  new Response(
    JSON.stringify({ error: { code, detail: "never expose raw failure" } }),
    { status },
  );
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function transportCapture(
  handler: (
    path: string,
    body: Record<string, unknown>,
    index: number,
  ) => Response | Promise<Response>,
) {
  const calls: {
    path: string;
    body: Record<string, unknown>;
    key: string | null;
    credentials: RequestCredentials | undefined;
  }[] = [];
  const transport: Transport = async (input, init) => {
    const call = {
      path: String(input),
      body: init?.body ? JSON.parse(String(init.body)) : {},
      key: new Headers(init?.headers).get("Idempotency-Key"),
      credentials: init?.credentials,
    };
    calls.push(call);
    return handler(call.path, call.body, calls.length);
  };
  return { api: new WikiApi(transport), calls };
}

describe("P11 explicit versioned task controls", () => {
  it.each(["resume", "cancel"] as const)(
    "confirms %s and retries the identical immutable request",
    async (action) => {
      const { api, calls } = transportCapture((_p, _b, index) => {
        if (index === 1) throw new Error("synthetic lost response");
        return envelope({
          ...task,
          state: action === "resume" ? "queued" : "cancelled",
          revision: 3,
        });
      });
      const user = userEvent.setup();
      render(<TaskControls api={api} task={task} reload={() => {}} />);
      await user.click(
        screen.getByRole("button", {
          name: action === "resume" ? "恢复任务" : "取消任务",
        }),
      );
      expect(calls).toHaveLength(0);
      await user.click(screen.getByRole("button", { name: "确认任务操作" }));
      await user.click(
        await screen.findByRole("button", { name: "重试同一请求" }),
      );
      await screen.findByText("已取得服务回执");
      expect(calls).toHaveLength(2);
      expect(calls[0]).toEqual(calls[1]);
      expect(calls[0].body).toEqual({ expected_revision: 2 });
      expect(calls[0].path).toBe(
        `/api/knowledge/v1/tasks/tsk_synthetic/${action}`,
      );
      expect(calls[0].credentials).toBe("same-origin");
      expect(screen.queryByText("任务已完成")).toBeNull();
    },
  );
  it.each(["running", "succeeded", "failed", "cancelled"])(
    "does not offer controls for %s",
    (state) => {
      render(
        <TaskControls
          api={new WikiApi()}
          task={{ ...task, state }}
          reload={() => {}}
        />,
      );
      expect(screen.queryByRole("button", { name: "恢复任务" })).toBeNull();
      expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
    },
  );
  it("blocks reconciliation tasks and stale responses without success", async () => {
    const { api } = transportCapture(() => failure("BASE_STALE", 409));
    const user = userEvent.setup();
    const result = render(
      <TaskControls
        api={api}
        task={{ ...task, reconciliation_required: true }}
        reload={() => {}}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    result.rerender(<TaskControls api={api} task={task} reload={() => {}} />);
    await user.click(screen.getByRole("button", { name: "恢复任务" }));
    await user.click(screen.getByRole("button", { name: "确认任务操作" }));
    await screen.findByText(/基础版本已变化/);
    expect(screen.queryByText("已取得服务回执")).toBeNull();
    expect(screen.queryByRole("button", { name: "重试同一请求" })).toBeNull();
  });
});

describe("P03 bounded source supplement", () => {
  it("keeps source text and note separate, freezes exact retry, and does not keep automatically", async () => {
    const { api, calls } = transportCapture((_p, _b, index) => {
      if (index === 1) throw new Error("uncertain");
      return envelope({ ...intake, state: "partial", preview_revision: 1 });
    });
    const user = userEvent.setup();
    const reload = vi.fn();
    render(<SupplementForm api={api} item={intake} reload={reload} />);
    await user.click(screen.getByRole("button", { name: "粘贴补充文本" }));
    await user.type(
      screen.getByLabelText("补充来源正文"),
      "Synthetic source only.",
    );
    await user.type(
      screen.getByLabelText("私人附言（不作为来源正文）"),
      "Synthetic private note.",
    );
    expect(
      (
        screen.getByRole("button", {
          name: "提交本次补料",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "提交本次补料" }));
    expect(
      (screen.getByLabelText("补充来源正文") as HTMLTextAreaElement).disabled,
    ).toBe(true);
    await user.click(
      await screen.findByRole("button", { name: "重试同一请求" }),
    );
    await screen.findByText("补料已写入临时预览，尚未保留");
    expect(calls[0]).toEqual(calls[1]);
    expect(calls[0].body).toEqual({
      expected_preview_revision: 0,
      text: "Synthetic source only.",
      private_note: "Synthetic private note.",
    });
    expect(calls.every((c) => c.path.endsWith("/supplement"))).toBe(true);
    expect(reload).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "查看新预览后再决定" }),
    );
    expect(reload).toHaveBeenCalledOnce();
  });
  it("rechecks expiry at the click even when the background display did not update", async () => {
    const { api, calls } = transportCapture(() => {
      throw new Error("must not send");
    });
    const user = userEvent.setup();
    const now = Date.now();
    render(
      <SupplementForm
        api={api}
        item={{ ...intake, expires_at: new Date(now + 60000).toISOString() }}
        reload={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: "粘贴补充文本" }));
    await user.type(screen.getByLabelText("补充来源正文"), "synthetic");
    await user.click(screen.getByRole("checkbox"));
    vi.spyOn(Date, "now").mockReturnValue(now + 60001);
    fireEvent.click(screen.getByRole("button", { name: "提交本次补料" }));
    expect(calls).toHaveLength(0);
    expect(screen.getByText("临时预览已到期，这次补料未发送。")).toBeTruthy();
  });
  it("does not offer supplementation after retention or expiry", () => {
    const { rerender } = render(
      <SupplementForm
        api={new WikiApi()}
        item={{ ...intake, source_id: "src_kept" }}
        reload={() => {}}
      />,
    );
    expect(
      (
        screen.getByRole("button", {
          name: "粘贴补充文本",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    rerender(
      <SupplementForm
        api={new WikiApi()}
        item={{ ...intake, expires_at: "2000-01-01T00:00:00Z" }}
        reload={() => {}}
      />,
    );
    expect(
      (
        screen.getByRole("button", {
          name: "粘贴补充文本",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });
  it("displays original text and note under separate provenance labels without executing HTML", () => {
    const { container } = render(
      <SupplementHistory
        item={{
          ...intake,
          preview: {
            manual_supplement: true,
            original_text: "Original synthetic text",
            supplements: [
              {
                preview_revision: 1,
                text: "New source",
                private_note: "<img src=x onerror=alert(1)>",
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText("补料前的来源文本")).toBeTruthy();
    expect(screen.getByText("私人附言 · 不属于来源事实")).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("P05 evidence reading and gated revision proposals", () => {
  function pendingEvidence() {
    const calls: {
      path: string;
      body: Record<string, unknown>;
      signal: AbortSignal;
      resolve: (response: Response) => void;
    }[] = [];
    const api = new WikiApi((input, init) => {
      return new Promise<Response>((resolve) => {
        calls.push({
          path: String(input),
          body: JSON.parse(String(init?.body)),
          signal: init?.signal as AbortSignal,
          resolve,
        });
      });
    });
    return { api, calls };
  }
  function evidenceResponse(body: Record<string, unknown>, text: string) {
    return envelope({
      kind: "source",
      source_id: body.source_id,
      revision: body.source_revision,
      content_hash: body.content_hash,
      text,
      text_basis: "retained_text",
    });
  }
  it("loads bound source only on demand and clears text when closed", async () => {
    const { api, calls } = transportCapture(() =>
      envelope({
        kind: "source",
        source_id: "src_synthetic",
        revision: 1,
        content_hash: "synthetic-hash",
        text: "Verified synthetic evidence",
        title: "",
        text_basis: "retained_text",
        body_complete: null,
        raw_path: "never render",
      }),
    );
    const user = userEvent.setup();
    render(
      <StrictMode>
        <EvidenceReader api={api} reference={entry.evidence_refs[0]} />
      </StrictMode>,
    );
    expect(calls).toHaveLength(0);
    await user.click(
      screen.getByRole("button", { name: "读取已授权来源文本" }),
    );
    await screen.findByText("Verified synthetic evidence");
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({
      source_id: "src_synthetic",
      source_revision: 1,
      content_hash: "synthetic-hash",
      locator: "line 1",
    });
    expect(calls[0].key).toBeNull();
    expect(screen.queryByText("never render")).toBeNull();
    await user.click(screen.getByRole("button", { name: "关闭来源文本" }));
    expect(screen.queryByText("Verified synthetic evidence")).toBeNull();
  });
  it("does not dispatch a read closed before its first microtask", async () => {
    const { api, calls } = pendingEvidence();
    render(
      <StrictMode>
        <EvidenceReader api={api} reference={entry.evidence_refs[0]} />
      </StrictMode>,
    );
    fireEvent.click(screen.getByRole("button", { name: "读取已授权来源文本" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭来源文本" }));
    await act(async () => {});
    expect(calls).toHaveLength(0);
    expect(screen.queryByRole("status")).toBeNull();
  });
  it("reauthorizes each explicit reopening and ignores the closed read's late response", async () => {
    const { api, calls } = pendingEvidence();
    const user = userEvent.setup();
    render(
      <StrictMode>
        <EvidenceReader api={api} reference={entry.evidence_refs[0]} />
      </StrictMode>,
    );
    await user.click(
      screen.getByRole("button", { name: "读取已授权来源文本" }),
    );
    expect(calls).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "关闭来源文本" }));
    expect(calls[0].signal.aborted).toBe(true);
    await user.click(
      screen.getByRole("button", { name: "读取已授权来源文本" }),
    );
    expect(calls).toHaveLength(2);
    expect(calls[1].body).toEqual(calls[0].body);
    expect(calls[1].signal.aborted).toBe(false);
    await act(async () => {
      calls[0].resolve(evidenceResponse(calls[0].body, "Closed read body"));
    });
    expect(screen.queryByText("Closed read body")).toBeNull();
    expect(screen.getByRole("status")).toBeTruthy();
    await act(async () => {
      calls[1].resolve(evidenceResponse(calls[1].body, "Reauthorized body"));
    });
    expect(screen.getByText("Reauthorized body")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "关闭来源文本" }));
    expect(screen.queryByText("Reauthorized body")).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "读取已授权来源文本" }),
    );
    expect(calls).toHaveLength(3);
    expect(screen.queryByText("Reauthorized body")).toBeNull();
  });
  it.each([false, true])(
    "binds a changed reference to a fresh read, never prior content (prior response settled: %s)",
    async (settled) => {
      const { api, calls } = pendingEvidence();
      const user = userEvent.setup();
      const view = (reference: (typeof entry.evidence_refs)[number]) => (
        <StrictMode>
          <EvidenceReader api={api} reference={reference} />
        </StrictMode>
      );
      const { rerender } = render(view(entry.evidence_refs[0]));
      await user.click(
        screen.getByRole("button", { name: "读取已授权来源文本" }),
      );
      expect(calls).toHaveLength(1);
      if (settled) {
        await act(async () => {
          calls[0].resolve(
            evidenceResponse(calls[0].body, "Prior reference body"),
          );
        });
        expect(screen.getByText("Prior reference body")).toBeTruthy();
      }
      const next = {
        ...entry.evidence_refs[0],
        source_revision: 2,
        content_hash: "next-synthetic-hash",
      };
      rerender(view(next));
      expect(screen.queryByText("Prior reference body")).toBeNull();
      expect(calls[0].signal.aborted).toBe(true);
      await act(async () => {});
      expect(calls).toHaveLength(2);
      expect(calls[1].path).toBe("/api/knowledge/v1/evidence/read");
      expect(calls[1].body).toEqual(next);
      await act(async () => {
        if (!settled)
          calls[0].resolve(
            evidenceResponse(calls[0].body, "Prior reference body"),
          );
        calls[1].resolve(
          evidenceResponse(calls[1].body, "Current reference body"),
        );
      });
      expect(screen.queryByText("Prior reference body")).toBeNull();
      expect(screen.getByText("Current reference body")).toBeTruthy();
      expect(calls).toHaveLength(2);
    },
  );
  it("reports a failed read without background retry, and retries only on explicit request", async () => {
    const { api, calls } = transportCapture((_path, body, index) =>
      index === 1
        ? failure("NETWORK_ERROR", 503)
        : evidenceResponse(body, "Explicit retry body"),
    );
    const user = userEvent.setup();
    render(
      <StrictMode>
        <EvidenceReader api={api} reference={entry.evidence_refs[0]} />
      </StrictMode>,
    );
    await user.click(
      screen.getByRole("button", { name: "读取已授权来源文本" }),
    );
    expect(await screen.findByRole("alert")).toBeTruthy();
    await act(async () => {});
    expect(calls).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("Explicit retry body")).toBeTruthy();
    expect(calls).toHaveLength(2);
    expect(calls[1]).toEqual(calls[0]);
  });
  it("rejects mismatched evidence binding without rendering foreign text", async () => {
    const { api } = transportCapture(() =>
      envelope({
        kind: "source",
        source_id: "src_foreign",
        revision: 1,
        content_hash: "synthetic-hash",
        text: "Must not display",
        text_basis: "retained_text",
      }),
    );
    render(<EvidenceReader api={api} reference={entry.evidence_refs[0]} />);
    fireEvent.click(screen.getByRole("button", { name: "读取已授权来源文本" }));
    await screen.findByText(/无法验证的数据/);
    expect(screen.queryByText("Must not display")).toBeNull();
  });
  it.each(["update", "delete"] as const)(
    "submits %s only as an exact revision proposal, never apply",
    async (operation) => {
      const { api, calls } = transportCapture((_p, body) =>
        envelope({
          ...entry,
          ...body,
          change_id: "chg_synthetic",
          state: "proposed",
          patch_hash: "synthetic-patch",
          risk_flags: [],
        }),
      );
      const user = userEvent.setup();
      render(<EntryProposal api={api} entry={entry} historical={false} />);
      await user.click(
        screen.getByRole("button", {
          name: operation === "update" ? "提出修改" : "提出删除",
        }),
      );
      if (operation === "update") {
        await user.clear(screen.getByLabelText("候选正文"));
        await user.type(
          screen.getByLabelText("候选正文"),
          "Synthetic revised body",
        );
      }
      expect(calls).toHaveLength(0);
      await user.click(screen.getByRole("checkbox"));
      await user.click(screen.getByRole("button", { name: "提交版本化提案" }));
      await screen.findByText("提案已创建，正式知识未改写");
      expect(calls).toHaveLength(1);
      expect(calls[0].path).toBe("/api/knowledge/v1/changes");
      expect(calls[0].body).toMatchObject({
        entry_id: entry.entry_id,
        base_revision: 3,
        operation,
        publication_intent: "public",
        evidence_refs: JSON.parse(JSON.stringify(entry.evidence_refs)),
      });
      expect(calls[0].body).not.toHaveProperty("user_approved");
      expect(calls[0].body).not.toHaveProperty("owner_id");
    },
  );
  it("keeps source and candidate text on uncertain proposal retry, clearing them on 401", async () => {
    let postCount = 0;
    const { api, calls } = transportCapture((path) => {
      if (path.includes("/entries/")) return envelope(entry);
      if (path.endsWith("/changes")) {
        postCount++;
        return postCount === 1
          ? failure("TEMPORARY_UNAVAILABLE", 503)
          : failure("UNAUTHENTICATED", 401);
      }
      return envelope([]);
    });
    window.location.hash = "#/entry/ent_synthetic";
    const user = userEvent.setup();
    render(<WikiApp api={api} />);
    await user.click(await screen.findByRole("button", { name: "提出修改" }));
    await user.type(
      screen.getByLabelText("候选正文"),
      " synthetic private draft",
    );
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "提交版本化提案" }));
    await user.click(
      await screen.findByRole("button", { name: "重试同一请求" }),
    );
    await waitFor(() => expect(screen.queryByLabelText("候选正文")).toBeNull());
    expect(screen.queryByText("正式基础正文")).toBeNull();
    const writes = calls.filter((c) => c.path.endsWith("/changes"));
    expect(writes[0]).toEqual(writes[1]);
  });
  it("does not allow modifying a historical revision", () => {
    render(<EntryProposal api={new WikiApi()} entry={entry} historical />);
    expect(
      (screen.getByRole("button", { name: "提出修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });
});

describe("private action dismissal", () => {
  it("ignores a supplement receipt arriving after the form was unmounted", async () => {
    let resolve!: (value: Response) => void;
    const promise = new Promise<Response>((done) => {
      resolve = done;
    });
    const { api } = transportCapture(() => promise);
    const user = userEvent.setup();
    const reload = vi.fn();
    const { unmount } = render(
      <SupplementForm api={api} item={intake} reload={reload} />,
    );
    await user.click(screen.getByRole("button", { name: "粘贴补充文本" }));
    await user.type(screen.getByLabelText("补充来源正文"), "synthetic");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "提交本次补料" }));
    unmount();
    resolve(envelope({ ...intake, state: "partial", preview_revision: 1 }));
    await promise;
    expect(reload).not.toHaveBeenCalled();
    expect(screen.queryByText("补料已写入临时预览，尚未保留")).toBeNull();
  });
});
