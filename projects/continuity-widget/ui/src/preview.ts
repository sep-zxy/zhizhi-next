import { emit } from "@tauri-apps/api/event";
import { api, onRevision, startRevisionPolling } from "./api";
import { hydrateIcons } from "./icons";
import type { ItemHorizon, ItemView, ProjectPreview } from "./types";

export function mountPreview(root: HTMLElement): void {
  let projectId = window.__previewProjectId ?? "";
  let data: ProjectPreview | null = null;
  let page = 0;
  let pageSize = 8;
  let closeTimer = 0;

  const load = async (): Promise<void> => {
    if (!projectId) return;
    try { data = await api.preview(projectId); page = 0; render(); }
    catch (error) { root.innerHTML = `<main class="preview-shell error-shell">${escapeHtml(String(error))}</main>`; }
  };

  const render = (): void => {
    if (!data) { root.innerHTML = `<main class="preview-shell loading-shell"><i data-lucide="refresh-cw"></i></main>`; hydrateIcons(); return; }
    const grouped = flattenGroups(data.project.items);
    const pages = Math.max(1, Math.ceil(grouped.length / pageSize));
    page = Math.min(page, pages - 1);
    const visible = grouped.slice(page * pageSize, page * pageSize + pageSize);
    const percent = data.project.progress.percent;
    const milestone = data.project.milestones.find((entry) => entry.status === "in_progress") ?? data.project.milestones.find((entry) => entry.status === "planned");
    root.innerHTML = `<main class="preview-shell">
      <header><div><strong>${escapeHtml(data.project.name)}</strong><span>${statusLabel(data.project.status)} · ${maturityLabel(data.project.maturityStage)}</span></div>${data.project.projectPath ? `<button data-action="open-folder"><i data-lucide="folder-open"></i></button>` : ""}</header>
      <div class="preview-project-meta"><p>${escapeHtml(data.project.goal || data.project.summary || "尚未填写项目目标")}</p><div><div class="mini-progress"><span style="width:${percent ?? 0}%"></span></div><strong>${percent === null ? "未规划" : `${percent}%`}</strong></div>${milestone ? `<small>当前里程碑：${escapeHtml(milestone.title)}</small>` : `<small>尚未登记里程碑</small>`}</div>
      <div class="preview-items">${visible.map((entry) => `<article><span>${entry.heading}</span><div><strong>${escapeHtml(entry.item.title)}</strong>${entry.item.details ? `<p>${escapeHtml(entry.item.details)}</p>` : ""}</div><small>${entry.item.activityScore}</small></article>`).join("") || `<div class="one-line-empty">暂无事项</div>`}</div>
      <footer><span>${data.project.items.filter((item) => item.status === "open" && item.kind === "action").length} Todo · ${data.project.items.filter((item) => item.status === "open" && item.kind === "question").length} 待确认</span>${pages > 1 ? `<div class="pager"><button data-action="prev"><i data-lucide="chevron-left"></i></button><small>${page + 1}/${pages}</small><button data-action="next"><i data-lucide="chevron-right"></i></button></div>` : ""}</footer>
    </main>`;
    root.querySelector<HTMLElement>("[data-action='open-folder']")?.addEventListener("click", () => void api.openProjectPath(projectId));
    root.querySelector<HTMLElement>("[data-action='prev']")?.addEventListener("click", () => { page = (page - 1 + pages) % pages; render(); });
    root.querySelector<HTMLElement>("[data-action='next']")?.addEventListener("click", () => { page = (page + 1) % pages; render(); });
    hydrateIcons();
    recompute();
  };

  const recompute = (): void => {
    const height = root.querySelector<HTMLElement>(".preview-items")?.clientHeight ?? 480;
    pageSize = Math.max(1, Math.floor(height / 60));
  };

  root.addEventListener("pointerenter", () => { window.clearTimeout(closeTimer); void emit("continuity://preview-hover", true); });
  root.addEventListener("pointerleave", () => { void emit("continuity://preview-hover", false); closeTimer = window.setTimeout(() => void api.hidePreview(), 250); });
  window.addEventListener("continuity-preview-project", (event) => { projectId = (event as CustomEvent<string>).detail; void load(); });
  void onRevision((revision) => { if (data && revision !== data.revision) void load(); });
  const stopPolling = startRevisionPolling(() => data?.revision ?? 0, (update) => { if (update.changed) void load(); });
  window.addEventListener("beforeunload", stopPolling);
  render(); void load();
}

function flattenGroups(items: ItemView[]): Array<{ heading: string; item: ItemView }> {
  const output: Array<{ heading: string; item: ItemView }> = [];
  for (const horizon of ["daily", "weekly", "later"] as ItemHorizon[]) {
    for (const kind of ["question", "action"] as const) {
      for (const item of items.filter((entry) => entry.horizon === horizon && entry.kind === kind && entry.status === "open")) {
        output.push({ heading: `${horizonLabel(horizon)} · ${kind === "question" ? "待确认" : "下一步"}`, item });
      }
    }
  }
  return output;
}

function horizonLabel(value: ItemHorizon): string { return value === "daily" ? "今日" : value === "weekly" ? "本周" : "稍后"; }
function statusLabel(value: string): string { return ({active:"活跃",paused:"暂停",candidate:"候选",completed:"完成",archived:"归档"} as Record<string,string>)[value] ?? value; }
function maturityLabel(value: string): string { return ({idea:"想法",defined:"已定义",executing:"执行中",validating:"验证中",mature:"成熟"} as Record<string,string>)[value] ?? value; }
function escapeHtml(value: string): string { return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
