import { isTrustedLearningViewer } from "./contracts";
import { useEffect, useState } from "react";
import { WikiApiError } from "./api";
import { fields, strings, text, type Entry, type Task } from "./contracts";

export interface Route {
  page: string;
  id: string;
  query: URLSearchParams;
}
export function parseRoute(hash: string): Route {
  const clean = hash.replace(/^#/, "") || "/";
  const [path, query = ""] = clean.split("?");
  const parts = path.split("/").filter(Boolean);
  let id = "";
  try {
    id = decodeURIComponent(parts[1] || "");
  } catch {
    return { page: "not-found", id: "", query: new URLSearchParams() };
  }
  const page = parts[0] || "home";
  return {
    page: parts.length > 2 ? "not-found" : page,
    id,
    query: new URLSearchParams(query),
  };
}
export function routeHref(
  page: string,
  id = "",
  query?: URLSearchParams,
): string {
  return (
    "#/" +
    page +
    (id ? "/" + encodeURIComponent(id) : "") +
    (query?.size ? "?" + query : "")
  );
}
export function useRoute(): Route {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const changed = () => setHash(window.location.hash);
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  return parseRoute(hash);
}
export function navigate(href: string) {
  window.location.hash = href.slice(1);
}
export type Resource<T> = {
  key: string;
  status: "loading" | "ready" | "error";
  data?: T;
  error?: WikiApiError;
  reload: () => void;
};
/** The old route's data is hidden immediately, before the new effect runs. */
export function useResource<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
): Resource<T> {
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<Omit<Resource<T>, "reload">>({
    key,
    status: "loading",
  });
  useEffect(() => {
    const controller = new AbortController();
    setState({ key, status: "loading" });
    void load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted)
          setState({ key, status: "ready", data });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setState({
            key,
            status: "error",
            error:
              error instanceof WikiApiError
                ? error
                : new WikiApiError("INVALID_RESPONSE", 0),
          });
      });
    return () => controller.abort();
  }, [key, retry]); // load intentionally follows the stable identity key.
  return {
    ...(state.key === key ? state : { key, status: "loading" as const }),
    reload: () => setRetry((v) => v + 1),
  };
}
export function categories(entry: Entry): string[] {
  const values = strings(entry.metadata.categories);
  return values.length ? values : ["未分类"];
}
export function tags(entry: Entry): string[] {
  return strings(entry.metadata.tags);
}
export function categoryCounts(
  entries: Entry[],
): { name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const entry of entries.filter((e) => !e.deleted))
    for (const category of new Set(categories(entry)))
      counts.set(category, (counts.get(category) || 0) + 1);
  return [...counts]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh"))
    .map(([name, count]) => ({ name, count }));
}
export const TASK_LABELS: Record<string, string> = {
  queued: "等待处理",
  running: "处理中",
  succeeded: "任务完成",
  partial: "部分完成",
  waiting_auth: "等待登录",
  waiting_quota: "等待额度",
  waiting_user: "等待本人决定",
  retry_wait: "等待重试",
  blocked: "已阻塞",
  failed: "处理失败",
  cancelled: "已取消",
  waiting_review: "等待复核",
  waiting_input: "需要补料",
};
export function taskLabel(state: string): string {
  return TASK_LABELS[state] || "未知状态（" + state + "）";
}
export function taskTone(state: string): string {
  return state === "succeeded"
    ? "success"
    : ["failed", "blocked", "partial"].includes(state)
      ? "danger"
      : ["queued", "running"].includes(state)
        ? "info"
        : "warning";
}
export function taskResult(task: Task): {
  entryId?: string;
  revision?: number;
  changeId?: string;
  label: string;
} {
  const result = fields(task.result);
  const entryId = text(result.entry_id) || undefined;
  const revision =
    typeof result.revision === "number" ? result.revision : undefined;
  const changeId = text(result.change_id) || undefined;
  const committed =
    task.state === "succeeded" &&
    !!entryId &&
    typeof revision === "number" &&
    revision > 0;
  return {
    entryId,
    revision,
    changeId,
    label: committed
      ? "服务返回了条目修订，可打开核对。"
      : task.state === "succeeded"
        ? "此任务已完成，不等于知识已正式提交或公开。"
        : "尚未确认正式知识提交。",
  };
}
export function formatTime(value: string | undefined): string {
  if (!value) return "未提供";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间不可用"
    : date.toLocaleString("zh-CN", { hour12: false });
}
export function publication(entry: Entry) {
  return {
    intent:
      entry.publication_intent ||
      text(entry.metadata.publication_intent) ||
      entry.visibility,
    eligible:
      entry.publication_eligible === true
        ? "资格通过"
        : entry.publication_eligible === false
          ? "未取得资格"
          : "未提供资格结果",
    published: "发布状态未提供",
  };
}
export function safeNavigation(value: unknown, fallback: string): string {
  const v = text(value);
  return /^\/(?!\/)/.test(v) && !/[\u0000-\u0020\\]/.test(v) ? v : fallback;
}

/** Host-provided same-origin entry; pass exact identity/revision only, never content or secrets. */
export function engineeringEntryHref(
  base: string,
  entryId: string,
  revision: number,
): string {
  if (
    (!safeNavigation(base, "") && !isTrustedLearningViewer(base)) ||
    !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}(?![\s\S])/.test(entryId) ||
    !Number.isSafeInteger(revision) ||
    revision < 1
  )
    return "";
  const pathname = base.split(/[?#]/, 1)[0];
  return (
    pathname +
    "#/growth?" +
    new URLSearchParams({
      view: "entry-context",
      entry_id: entryId,
      revision: String(revision),
    })
  );
}
