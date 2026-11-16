import { getCurrentWindow } from "@tauri-apps/api/window";
import { api, onRevision, startRevisionPolling } from "./api";
import { hydrateIcons } from "./icons";
import type { DashboardView, ItemView, ProjectDashboardView } from "./types";
import { WidgetState } from "./widget-state";

const fixtureMode = new URLSearchParams(window.location.search).has("fixture");
const windowHandle = fixtureMode ? null : getCurrentWindow();

export function mountWidget(root: HTMLElement): void {
  const state = new WidgetState();
  let data: DashboardView | null = null;
  let loading = true;
  let message = "";

  const load = async (): Promise<void> => {
    try {
      data = await api.dashboard();
      state.reconcile(data);
      message = "";
    } catch (error) {
      message = String(error);
    } finally {
      loading = false;
      render();
    }
  };

  const render = (): void => {
    if (loading) {
      root.innerHTML = `<main class="widget-shell widget-state"><i data-lucide="loader-circle"></i><span>正在读取项目索引</span></main>`;
      hydrateIcons();
      return;
    }
    if (!data) {
      root.innerHTML = `<main class="widget-shell widget-state"><strong>数据库暂时不可用</strong><span>${escapeHtml(message)}</span><button data-action="refresh">重试</button></main>`;
      bind();
      return;
    }

    const selected = state.project(data);
    const actions = state.actions(data);
    const questions = state.questions(data);
    root.innerHTML = `<main class="widget-shell">
      <header class="widget-titlebar" data-tauri-drag-region>
        <div>${selected ? `<strong>${escapeHtml(selected.project.name)}</strong><span>${maturityLabel(selected.project.maturityStage)} · ${progressLabel(selected)}</span>` : `<strong>Continuity</strong><span>尚无项目</span>`}</div>
        <nav>
          <button data-action="refresh" title="刷新"><i data-lucide="refresh-cw"></i></button>
          <button data-action="open-workspace" title="打开项目工作区"><i data-lucide="panel-top-open"></i></button>
          <button data-action="hide" title="隐藏"><i data-lucide="minus"></i></button>
        </nav>
      </header>
      ${selected ? `<div class="widget-progress"><span style="width:${selected.progress.percent ?? 0}%"></span></div>` : ""}
      <div class="widget-content">
        ${renderSection("现在做", actions, "action")}
        ${renderSection("待确认", questions, "question")}
      </div>
      ${renderProjectSwitcher(data, state.selectedProjectId)}
    </main>`;
    bind();
    hydrateIcons();
  };

  const bind = (): void => {
    root.querySelectorAll<HTMLElement>("[data-action]").forEach((element) => element.addEventListener("click", (event) => {
      const action = element.dataset.action;
      if (action === "refresh") void load();
      else if (action === "open-workspace") void api.openEditor();
      else if (action === "hide") void windowHandle?.hide();
      else if (action === "select-project") { state.selectedProjectId = element.dataset.id ?? ""; render(); }
      else if (action === "complete" && data) {
        event.stopPropagation();
        void api.completeItem(element.dataset.id ?? "", data.revision).then(load).catch((error) => { message = String(error); render(); });
      }
    }));
    root.querySelectorAll<HTMLElement>("[data-project-preview]").forEach((element) => {
      element.addEventListener("pointerenter", () => void api.showPreview(element.dataset.projectPreview ?? ""));
      element.addEventListener("pointerleave", () => void api.hidePreview());
    });
  };

  if (!fixtureMode) {
    void onRevision((revision) => { if (data && revision !== data.revision) void load(); });
    const stopPolling = startRevisionPolling(() => data?.revision ?? 0, (update) => { if (update.changed) void load(); });
    windowHandle?.onMoved(() => void api.captureWindowState());
    windowHandle?.onResized(() => void api.captureWindowState());
    window.addEventListener("beforeunload", stopPolling);
  }
  void load();
}

function renderSection(title: string, items: ItemView[], kind: "action" | "question"): string {
  return `<section class="compact-section">
    <header><strong>${title}</strong><span>${items.length}</span></header>
    <div class="compact-list">${items.map((item) => `<div class="compact-row">
      ${kind === "action" ? `<button data-action="complete" data-id="${item.id}" class="compact-status" title="标记完成"><i data-lucide="circle"></i></button>` : `<span class="compact-status question"><i data-lucide="circle-help"></i></span>`}
      <button data-action="open-workspace" class="compact-title">${item.blocking ? `<i data-lucide="alert-circle"></i>` : ""}<strong>${escapeHtml(item.title)}</strong></button>
      <span class="priority-mark priority-${priorityBand(item.priority)}"></span>
    </div>`).join("") || `<div class="compact-empty">${kind === "action" ? "当前项目没有待执行 Todo" : "当前项目没有待确认事项"}</div>`}</div>
  </section>`;
}

function renderProjectSwitcher(data: DashboardView, selectedId: string): string {
  const selected = data.projects.find((entry) => entry.project.id === selectedId);
  return `<footer class="project-switcher">
    <div class="project-switcher-label"><span>项目</span>${selected ? `<strong>${escapeHtml(selected.project.name)}</strong>` : `<strong>未选择</strong>`}</div>
    <div class="project-counts"><span>Todo <strong>${selected?.openTodoCount ?? 0}</strong></span><span>待确认 <strong>${selected?.openQuestionCount ?? 0}</strong></span><span>里程碑 <strong>${selected?.progress.completedMilestones ?? 0}/${selected?.progress.totalMilestones ?? 0}</strong></span></div>
    <div class="project-tabs">${data.projects.map((entry) => `<button data-action="select-project" data-id="${entry.project.id}" data-project-preview="${entry.project.id}" class="${entry.project.id === selectedId ? "is-active" : ""}" title="${escapeAttr(entry.project.name)}"><span></span>${escapeHtml(entry.project.name)}</button>`).join("")}</div>
  </footer>`;
}

function progressLabel(project: ProjectDashboardView): string {
  return project.progress.percent === null ? "未规划" : `${project.progress.percent}%`;
}

function maturityLabel(value: string): string {
  return ({ idea: "想法", defined: "已定义", executing: "执行中", validating: "验证中", mature: "成熟" } as Record<string, string>)[value] ?? value;
}

function priorityBand(priority: number): string { return priority >= 14 ? "high" : priority >= 7 ? "medium" : "low"; }
function escapeHtml(value: string): string { return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
function escapeAttr(value: string): string { return escapeHtml(value); }
