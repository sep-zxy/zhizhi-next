import { api, onRevision, startRevisionPolling } from "./api";
import {
  boardGroups, EditorState,
  type FixedView, type LayoutMode, type ProjectPage, type ProjectScope,
} from "./editor-state";
import { hydrateIcons } from "./icons";
import type {
  EditorView, ItemStatus, ItemView, MilestoneStatus, MaturityStage, ProjectMilestone,
  ProjectStatus, ProjectView, Suggestion,
} from "./types";

export function mountEditor(root: HTMLElement): void {
  let data: EditorView | null = null;
  const state = new EditorState();
  let message = "";

  const currentProject = (): ProjectView | null => state.project(data);
  const currentItem = (): ItemView | null => state.item(data);

  const load = async (keepSelection = true): Promise<void> => {
    try {
      const next = await api.editor();
      data = next;
      state.reconcile(next, keepSelection);
      state.draftDirty = false;
      message = "";
      render();
    } catch (error) {
      message = String(error);
      render();
    }
  };

  const render = (): void => {
    const project = currentProject();
    const item = currentItem();
    root.innerHTML = `
      <main class="editor-shell">
        ${renderRail()}
        ${renderSidebar(data, state.selectedProjectId, state.projectScope, state.projectSearch)}
        <section class="workspace-shell">
          ${renderWorkspaceBar(project, state.page, message, data?.revision ?? 0, state.layout, state.filterOpen, state.conflictRevision)}
          ${project ? renderProjectNav(state.page) : ""}
          <div class="workspace-body ${item ? "has-peek" : ""}">
            <section class="workspace-content">${project ? renderPage(project, state) : renderEmpty()}</section>
            ${project && item ? renderPeek(project, item) : ""}
          </div>
        </section>
      </main>`;
    bind();
    hydrateIcons();
  };

  const bind = (): void => {
    root.querySelectorAll<HTMLElement>("[data-action]").forEach((element) => element.addEventListener("click", handleAction));
    root.querySelector<HTMLInputElement>("[data-role='project-search']")?.addEventListener("input", (event) => {
      state.projectSearch = (event.currentTarget as HTMLInputElement).value;
      render();
    });
    root.querySelectorAll<HTMLSelectElement>("[data-filter]").forEach((element) => element.addEventListener("change", () => {
      const filter = element.dataset.filter;
      if (filter === "kind") state.filters.kind = element.value as typeof state.filters.kind;
      else if (filter === "milestoneId") state.filters.milestoneId = element.value;
      else if (filter === "priority") state.filters.priority = element.value as typeof state.filters.priority;
      render();
    }));
    root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>("[data-project-field]").forEach((element) => {
      element.addEventListener("input", () => { state.draftDirty = true; });
      element.addEventListener(element instanceof HTMLSelectElement ? "change" : "blur", () => void saveProjectField(element));
    });
    root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>("[data-item-field]").forEach((element) => {
      element.addEventListener("input", () => { state.draftDirty = true; });
      element.addEventListener(element instanceof HTMLSelectElement || element.type === "checkbox" ? "change" : "blur", () => void saveItemField(element));
    });
    root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>("[data-milestone-field]").forEach((element) => {
      element.addEventListener(element instanceof HTMLSelectElement ? "change" : "blur", () => void saveMilestoneField(element));
    });
  };

  const handleAction = (event: Event): void => {
    const target = event.currentTarget as HTMLElement;
    const action = target.dataset.action;
    if (action === "project-scope") { state.projectScope = (target.dataset.scope as ProjectScope) ?? "all"; render(); return; }
    if (action === "select-project") { state.selectedProjectId = target.dataset.id ?? ""; state.selectedItemId = ""; render(); return; }
    if (action === "page") { state.page = (target.dataset.page as ProjectPage) ?? "work-items"; state.selectedItemId = ""; render(); return; }
    if (action === "fixed-view") { state.fixedView = (target.dataset.view as FixedView) ?? "todo"; state.selectedItemId = ""; render(); return; }
    if (action === "layout") { state.layout = (target.dataset.layout as LayoutMode) ?? "list"; render(); return; }
    if (action === "toggle-filter") { state.filterOpen = !state.filterOpen; render(); return; }
    if (action === "reset-filter") { state.filters = { kind: "all", milestoneId: "all", priority: "all" }; render(); return; }
    if (action === "select-item") { state.selectedItemId = target.dataset.id ?? ""; render(); return; }
    if (action === "close-peek") { state.selectedItemId = ""; render(); return; }
    if (action === "reload") { state.draftDirty = false; void load(); return; }
    if (action === "open-folder") { void api.openProjectPath(state.selectedProjectId).catch(showError); return; }
    if (action === "confirm-project" && data) { void mutate(() => api.confirmProject(state.selectedProjectId, data!.revision)); return; }
    if (action === "add-item" && data) {
      void mutate(async () => {
        const result = await api.quickAdd("新事项", state.selectedProjectId, data!.revision);
        state.selectedItemId = result.value.id;
        return result;
      });
      return;
    }
    if (action === "complete-item" && data) { event.stopPropagation(); void mutate(() => api.completeItem(target.dataset.id ?? "", data!.revision)); return; }
    if (action === "add-milestone" && data) {
      const project = currentProject();
      if (!project) return;
      void mutate(() => api.upsertMilestone({
        projectId: project.id,
        fingerprint: `manual-${Date.now()}`,
        title: "新里程碑",
        acceptanceCriteria: "",
        weight: 1,
        sortOrder: project.milestones.length,
        expectedRevision: data!.revision,
      }));
      return;
    }
    if ((action === "apply-suggestion" || action === "reject-suggestion") && data) {
      event.stopPropagation();
      const id = target.dataset.id ?? "";
      void mutate(() => action === "apply-suggestion" ? api.applySuggestion(id, data!.revision) : api.rejectSuggestion(id, data!.revision));
    }
  };

  const mutate = async (operation: () => Promise<unknown>): Promise<void> => {
    message = "正在保存";
    renderStatus();
    try {
      await operation();
      await load();
    } catch (error) {
      showError(error);
    }
  };

  const saveProjectField = async (element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): Promise<void> => {
    const project = currentProject();
    if (!data || !project) return;
    const field = element.dataset.projectField;
    const value = element.value;
    if (field === "name") await mutate(() => api.saveProject({ id: project.id, name: value, expectedRevision: data!.revision }));
    else if (field === "summary") await mutate(() => api.saveProject({ id: project.id, summary: value, expectedRevision: data!.revision }));
    else if (field === "goal") await mutate(() => api.saveProject({ id: project.id, goal: value, expectedRevision: data!.revision }));
    else if (field === "successCriteria") await mutate(() => api.saveProject({ id: project.id, successCriteria: value, expectedRevision: data!.revision }));
    else if (field === "status") await mutate(() => api.saveProject({ id: project.id, status: value as ProjectStatus, expectedRevision: data!.revision }));
    else if (field === "maturityStage") await mutate(() => api.saveProject({ id: project.id, maturityStage: value as MaturityStage, expectedRevision: data!.revision }));
    else if (field === "projectPath") await mutate(() => api.saveProject({ id: project.id, projectPath: value.trim() || null, expectedRevision: data!.revision }));
  };

  const saveItemField = async (element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): Promise<void> => {
    const item = currentItem();
    if (!data || !item) return;
    const field = element.dataset.itemField;
    const checked = element instanceof HTMLInputElement && element.type === "checkbox" ? element.checked : false;
    if (field === "title") await mutate(() => api.saveItem({ id: item.id, title: element.value, expectedRevision: data!.revision }));
    else if (field === "details") await mutate(() => api.saveItem({ id: item.id, details: element.value, expectedRevision: data!.revision }));
    else if (field === "kind") await mutate(() => api.saveItem({ id: item.id, kind: element.value as ItemView["kind"], expectedRevision: data!.revision }));
    else if (field === "horizon") await mutate(() => api.saveItem({ id: item.id, horizon: element.value as ItemView["horizon"], expectedRevision: data!.revision }));
    else if (field === "status") await mutate(() => api.saveItem({ id: item.id, status: element.value as ItemStatus, expectedRevision: data!.revision }));
    else if (field === "priority") await mutate(() => api.saveItem({ id: item.id, priority: Number(element.value), expectedRevision: data!.revision }));
    else if (field === "milestoneId") await mutate(() => api.saveItem({ id: item.id, milestoneId: element.value || null, expectedRevision: data!.revision }));
    else if (field === "blocking") await mutate(() => api.saveItem({ id: item.id, blocking: checked, expectedRevision: data!.revision }));
    else if (field === "pinned") await mutate(() => api.saveItem({ id: item.id, pinned: checked, expectedRevision: data!.revision }));
  };

  const saveMilestoneField = async (element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): Promise<void> => {
    if (!data) return;
    const id = element.dataset.milestoneId ?? "";
    const field = element.dataset.milestoneField;
    if (field === "title") await mutate(() => api.saveMilestone({ id, title: element.value, expectedRevision: data!.revision }));
    else if (field === "acceptanceCriteria") await mutate(() => api.saveMilestone({ id, acceptanceCriteria: element.value, expectedRevision: data!.revision }));
    else if (field === "status") await mutate(() => api.saveMilestone({ id, status: element.value as MilestoneStatus, expectedRevision: data!.revision }));
    else if (field === "weight") await mutate(() => api.saveMilestone({ id, weight: Number(element.value), expectedRevision: data!.revision }));
  };

  const renderStatus = (): void => {
    const element = root.querySelector<HTMLElement>("[data-role='save-state']");
    if (element) element.textContent = message;
  };

  const showError = (error: unknown): void => {
    if (String(error).includes("revision_conflict")) {
      state.conflictRevision = data?.revision ?? 0;
      message = "草稿未覆盖，数据源已有新版本";
      render();
    } else {
      message = String(error);
      renderStatus();
    }
  };

  void onRevision((revision) => {
    if (!data || revision === data.revision) return;
    if (state.noteExternalRevision(revision)) { message = "外部数据已更新，当前草稿仍被保留"; render(); }
    else void load();
  });
  const stopPolling = startRevisionPolling(() => data?.revision ?? 0, (update) => {
    if (!update.changed) return;
    if (state.noteExternalRevision(update.revision)) { message = "外部数据已更新，当前草稿仍被保留"; render(); }
    else void load();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.selectedItemId) { state.selectedItemId = ""; render(); }
  });
  window.addEventListener("beforeunload", stopPolling);
  void load(false);
}

function renderRail(): string {
  return `<nav class="app-rail" aria-label="全局导航">
    <div class="rail-mark" title="Continuity Widget">CW</div>
    <button class="rail-button is-active" title="项目"><i data-lucide="target"></i></button>
    <button class="rail-button" title="搜索"><i data-lucide="search"></i></button>
    <span class="rail-spacer"></span>
    <button class="rail-button" title="设置"><i data-lucide="settings"></i></button>
  </nav>`;
}

function renderSidebar(data: EditorView | null, selectedId: string, scope: ProjectScope, search: string): string {
  const all = data?.projects ?? [];
  const candidates = all.filter((project) => project.status === "candidate").length;
  const projects = all.filter((project) => {
    const scopeMatch = scope === "all" || project.status === "candidate";
    const text = `${project.name} ${project.goal} ${project.summary}`.toLowerCase();
    return scopeMatch && (!search || text.includes(search.toLowerCase()));
  });
  return `<aside class="project-sidebar">
    <div class="sidebar-title"><strong>项目</strong><button class="icon-button" title="收起侧栏"><i data-lucide="panel-left-close"></i></button></div>
    <label class="project-search"><i data-lucide="search"></i><input data-role="project-search" value="${escapeAttr(search)}" placeholder="搜索项目"></label>
    <div class="sidebar-views">
      <button data-action="project-scope" data-scope="all" class="${scope === "all" ? "is-active" : ""}"><span>全部项目</span><small>${all.length}</small></button>
      <button data-action="project-scope" data-scope="candidate" class="${scope === "candidate" ? "is-active" : ""}"><span>候选项目</span><small>${candidates}</small></button>
    </div>
    <div class="sidebar-label">当前项目</div>
    <div class="project-list">${projects.map((project) => {
      const open = project.items.filter((item) => item.status === "open").length;
      return `<button data-action="select-project" data-id="${project.id}" class="project-row ${project.id === selectedId ? "is-active" : ""}"><span class="project-color"></span><strong>${escapeHtml(project.name)}</strong><small>${open}</small></button>`;
    }).join("") || `<div class="empty-row">没有匹配项目</div>`}</div>
  </aside>`;
}

function renderWorkspaceBar(project: ProjectView | null, page: ProjectPage, message: string, revision: number, layout: LayoutMode, filterOpen: boolean, conflictRevision: number | null): string {
  return `<header class="workspace-bar">
    <div class="breadcrumbs"><strong>${project ? escapeHtml(project.name) : "项目"}</strong><span>/</span><span>${pageLabel(page)}</span></div>
    <div class="workspace-actions">
      <span class="save-state" data-role="save-state">${escapeHtml(message || `revision ${revision}`)}</span>
      ${page === "work-items" ? `<div class="segmented" aria-label="视图方式"><button data-action="layout" data-layout="list" class="${layout === "list" ? "is-active" : ""}" title="列表"><i data-lucide="layout-list"></i></button><button data-action="layout" data-layout="board" class="${layout === "board" ? "is-active" : ""}" title="看板"><i data-lucide="columns-3"></i></button></div><button data-action="toggle-filter" class="icon-button ${filterOpen ? "is-active" : ""}" title="筛选"><i data-lucide="filter"></i></button>` : ""}
      <button class="icon-button" title="更多操作"><i data-lucide="more-horizontal"></i></button>
    </div>
    ${conflictRevision === null ? "" : `<div class="revision-conflict"><i data-lucide="git-compare-arrows"></i><span>数据库已更新到 revision ${conflictRevision}，当前未提交草稿仍保留。</span><button data-action="reload">放弃草稿并刷新</button></div>`}
  </header>`;
}

function renderProjectNav(page: ProjectPage): string {
  const pages: Array<[ProjectPage, string]> = [["overview", "概览"], ["work-items", "工作项"], ["milestones", "里程碑"], ["activity", "活动"]];
  return `<nav class="project-nav">${pages.map(([id, label]) => `<button data-action="page" data-page="${id}" class="${page === id ? "is-active" : ""}">${label}</button>`).join("")}</nav>`;
}

function renderPage(project: ProjectView, state: EditorState): string {
  if (state.page === "overview") return renderOverview(project);
  if (state.page === "milestones") return renderMilestones(project);
  if (state.page === "activity") return renderActivity(project);
  return renderWorkItems(project, state);
}

function renderOverview(project: ProjectView): string {
  const percent = project.progress.percent;
  return `<div class="overview-page">
    <section class="project-heading-band">
      <div><input class="project-name-input" data-project-field="name" value="${escapeAttr(project.name)}"><div class="project-badges"><span>${statusLabel(project.status)}</span><span>${maturityLabel(project.maturityStage)}</span></div></div>
      <textarea class="goal-input" data-project-field="goal" placeholder="一句项目目标">${escapeHtml(project.goal)}</textarea>
      <div class="progress-line"><div class="progress-track"><span style="width:${percent ?? 0}%"></span></div><strong>${percent === null ? "未规划" : `${percent}%`}</strong><small>${project.progress.completedMilestones}/${project.progress.totalMilestones} 里程碑</small></div>
    </section>
    <section class="overview-band"><h2>项目定义</h2><label><span>摘要</span><textarea data-project-field="summary">${escapeHtml(project.summary)}</textarea></label><label><span>成功标准</span><textarea data-project-field="successCriteria">${escapeHtml(project.successCriteria)}</textarea></label><div class="inline-fields"><label><span>状态</span><select data-project-field="status">${projectStatusOptions(project.status)}</select></label><label><span>成熟度</span><select data-project-field="maturityStage">${maturityOptions(project.maturityStage)}</select></label><label><span>项目目录</span><div class="path-control"><input data-project-field="projectPath" value="${escapeAttr(project.projectPath ?? "")}"><button data-action="open-folder" class="icon-button" ${project.projectPath ? "" : "disabled"} title="打开目录"><i data-lucide="folder-open"></i></button></div></label></div>${project.status === "candidate" ? `<button data-action="confirm-project" class="command-button">确认项目</button>` : ""}</section>
    <section class="overview-band"><div class="band-heading"><h2>里程碑</h2><button data-action="page" data-page="milestones">查看全部</button></div>${renderMilestoneSummary(project.milestones)}</section>
  </div>`;
}

function renderWorkItems(project: ProjectView, state: EditorState): string {
  const counts = itemCounts(project);
  const items = state.visibleItems(project);
  return `<div class="work-items-page">
    <div class="work-items-title"><div><h1>${escapeHtml(project.name)}</h1><span>${items.length} 项</span></div><button data-action="add-item" class="command-button"><i data-lucide="plus"></i>新增事项</button></div>
    <nav class="fixed-views">${fixedViewButton("todo", "Todo", counts.todo, state.fixedView)}${fixedViewButton("questions", "待确认", counts.questions, state.fixedView)}${fixedViewButton("blocking", "阻塞", counts.blocking, state.fixedView)}${fixedViewButton("suggestions", "自动建议", counts.suggestions, state.fixedView)}${fixedViewButton("completed", "已完成", counts.completed, state.fixedView)}</nav>
    ${state.filterOpen ? renderFilters(project, state) : ""}
    ${state.hasFilters() ? renderFilterChips(project, state) : ""}
    ${state.layout === "list" ? renderItemList(project, items) : renderBoard(project, items)}
  </div>`;
}

function renderFilters(project: ProjectView, state: EditorState): string {
  return `<div class="filter-popover">
    <label>类型<select data-filter="kind"><option value="all" ${state.filters.kind === "all" ? "selected" : ""}>全部类型</option><option value="action" ${state.filters.kind === "action" ? "selected" : ""}>Todo</option><option value="question" ${state.filters.kind === "question" ? "selected" : ""}>待确认</option></select></label>
    <label>里程碑<select data-filter="milestoneId"><option value="all">全部里程碑</option><option value="unassigned" ${state.filters.milestoneId === "unassigned" ? "selected" : ""}>未分配</option>${project.milestones.map((milestone) => `<option value="${milestone.id}" ${state.filters.milestoneId === milestone.id ? "selected" : ""}>${escapeHtml(milestone.title)}</option>`).join("")}</select></label>
    <label>优先级<select data-filter="priority"><option value="all">全部优先级</option><option value="high" ${state.filters.priority === "high" ? "selected" : ""}>高</option><option value="medium" ${state.filters.priority === "medium" ? "selected" : ""}>中</option><option value="low" ${state.filters.priority === "low" ? "selected" : ""}>低</option></select></label>
  </div>`;
}

function renderFilterChips(project: ProjectView, state: EditorState): string {
  const chips: string[] = [];
  if (state.filters.kind !== "all") chips.push(state.filters.kind === "action" ? "类型：Todo" : "类型：待确认");
  if (state.filters.milestoneId !== "all") chips.push(`里程碑：${state.filters.milestoneId === "unassigned" ? "未分配" : milestoneName(project, state.filters.milestoneId)}`);
  if (state.filters.priority !== "all") chips.push(`优先级：${({ high: "高", medium: "中", low: "低" } as const)[state.filters.priority]}`);
  return `<div class="filter-chips">${chips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join("")}<button data-action="reset-filter">清除</button></div>`;
}

function renderItemList(project: ProjectView, items: ItemView[]): string {
  return `<div class="items-table"><div class="item-table-head"><span>状态</span><span>标题</span><span>里程碑</span><span>优先级</span><span>更新</span></div>${items.map((item) => {
    const suggestion = item.suggestions.find((entry) => entry.status === "pending");
    return `<div data-action="select-item" data-id="${item.id}" class="item-row"><button data-action="complete-item" data-id="${item.id}" class="status-button" title="标记完成" ${item.status !== "open" ? "disabled" : ""}>${statusIcon(item)}</button><div class="item-title-cell">${item.blocking ? `<i data-lucide="alert-circle" class="blocking-icon"></i>` : ""}<strong>${escapeHtml(item.title)}</strong></div><span>${escapeHtml(milestoneName(project, item.milestoneId))}</span><span>${priorityLabel(item.priority)}</span><time>${formatTime(item.lastEvidenceAt ?? item.closedAt ?? project.updatedAt)}</time>${suggestion ? `<div class="row-actions"><button data-action="apply-suggestion" data-id="${suggestion.id}" title="应用建议"><i data-lucide="check"></i></button><button data-action="reject-suggestion" data-id="${suggestion.id}" title="拒绝建议"><i data-lucide="x"></i></button></div>` : ""}</div>`;
  }).join("") || `<div class="empty-row table-empty">当前视图暂无事项 <button data-action="add-item">新增事项</button></div>`}</div>`;
}

function renderBoard(project: ProjectView, items: ItemView[]): string {
  return `<div class="board">${boardGroups(items).map((group) => `<section class="board-column" data-group="${group.id}"><header><strong>${group.label}</strong><span>${group.items.length}</span><button data-action="add-item" title="新增事项"><i data-lucide="plus"></i></button></header><div>${group.items.map((item) => `<button data-action="select-item" data-id="${item.id}" class="board-card"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(milestoneName(project, item.milestoneId))}</span><small>${priorityLabel(item.priority)} · ${formatTime(item.lastEvidenceAt ?? project.updatedAt)}</small></button>`).join("") || `<div class="board-empty">暂无事项</div>`}</div></section>`).join("")}</div>`;
}

function renderMilestones(project: ProjectView): string {
  return `<div class="milestones-page"><div class="page-title"><div><h1>里程碑</h1><span>${project.progress.percent === null ? "未规划" : `${project.progress.percent}%`} · ${project.progress.completedMilestones}/${project.progress.totalMilestones}</span></div><button data-action="add-milestone" class="command-button"><i data-lucide="plus"></i>新增里程碑</button></div><div class="milestone-table"><div class="milestone-head"><span>状态</span><span>标题</span><span>权重</span><span>完成条件</span></div>${project.milestones.map(renderMilestoneRow).join("") || `<div class="empty-row table-empty">尚未登记里程碑 <button data-action="add-milestone">新增里程碑</button></div>`}</div></div>`;
}

function renderMilestoneRow(milestone: ProjectMilestone): string {
  return `<div class="milestone-row"><select data-milestone-field="status" data-milestone-id="${milestone.id}">${milestoneStatusOptions(milestone.status)}</select><input data-milestone-field="title" data-milestone-id="${milestone.id}" value="${escapeAttr(milestone.title)}"><input type="number" min="1" max="100" data-milestone-field="weight" data-milestone-id="${milestone.id}" value="${milestone.weight}"><input data-milestone-field="acceptanceCriteria" data-milestone-id="${milestone.id}" value="${escapeAttr(milestone.acceptanceCriteria)}" placeholder="明确完成条件"></div>`;
}

function renderActivity(project: ProjectView): string {
  const entries = [
    ...project.history.map((entry) => ({ time: entry.createdAt, icon: "clock-3", title: historyLabel(entry.changeKind), meta: `revision ${entry.revision}` })),
    ...project.events.map((entry) => ({ time: entry.createdAt, icon: "sparkles", title: eventLabel(entry.eventType), meta: `${entry.source} · ${dispositionLabel(entry.disposition)}` })),
  ].sort((a, b) => b.time.localeCompare(a.time));
  return `<div class="activity-page"><div class="page-title"><div><h1>活动</h1><span>项目修改、证据与工程事件</span></div></div><div class="activity-stream">${entries.map((entry) => `<article><i data-lucide="${entry.icon}"></i><div><strong>${escapeHtml(entry.title)}</strong><span>${escapeHtml(entry.meta)}</span></div><time>${formatTime(entry.time)}</time></article>`).join("") || `<div class="empty-row">暂无活动</div>`}</div></div>`;
}

function renderPeek(project: ProjectView, item: ItemView): string {
  const suggestions = item.suggestions.filter((suggestion) => suggestion.status === "pending");
  return `<aside class="side-peek"><header><span>${statusIcon(item)}<strong>CW-${shortId(item.id)}</strong></span><div><button class="icon-button" title="全屏"><i data-lucide="maximize-2"></i></button><button data-action="close-peek" class="icon-button" title="关闭"><i data-lucide="x"></i></button></div></header><div class="peek-scroll"><textarea class="peek-title" data-item-field="title">${escapeHtml(item.title)}</textarea><section class="peek-section"><h3>详情</h3><textarea data-item-field="details" placeholder="补充上下文与完成条件">${escapeHtml(item.details)}</textarea></section><section class="peek-properties"><label><span>类型</span><select data-item-field="kind"><option value="action" ${item.kind === "action" ? "selected" : ""}>Todo</option><option value="question" ${item.kind === "question" ? "selected" : ""}>待确认</option></select></label><label><span>里程碑</span><select data-item-field="milestoneId"><option value="">未分配</option>${project.milestones.map((milestone) => `<option value="${milestone.id}" ${item.milestoneId === milestone.id ? "selected" : ""}>${escapeHtml(milestone.title)}</option>`).join("")}</select></label><label><span>Blocking</span><input type="checkbox" data-item-field="blocking" ${item.blocking ? "checked" : ""}></label><label><span>优先级</span><input type="number" min="0" max="20" data-item-field="priority" value="${item.priority}"></label><label><span>周期</span><select data-item-field="horizon"><option value="daily" ${item.horizon === "daily" ? "selected" : ""}>今日</option><option value="weekly" ${item.horizon === "weekly" ? "selected" : ""}>本周</option><option value="later" ${item.horizon === "later" ? "selected" : ""}>稍后</option></select></label><label><span>状态</span><select data-item-field="status"><option value="open" ${item.status === "open" ? "selected" : ""}>Open</option><option value="done" ${item.status === "done" ? "selected" : ""}>Done</option><option value="dismissed" ${item.status === "dismissed" ? "selected" : ""}>Dismissed</option></select></label><label><span>置顶</span><input type="checkbox" data-item-field="pinned" ${item.pinned ? "checked" : ""}></label></section>${suggestions.length ? `<section class="peek-section"><h3>自动建议</h3>${suggestions.map(renderSuggestion).join("")}</section>` : ""}<section class="peek-section"><h3>证据与活动</h3><div class="evidence-list">${item.evidence.map((entry) => `<article><strong>${escapeHtml(entry.summary)}</strong><span>${escapeHtml(entry.sourceRef)}</span><time>${formatTime(entry.occurredAt)}</time></article>`).join("") || `<div class="empty-row">暂无证据</div>`}</div></section></div></aside>`;
}

function renderSuggestion(suggestion: Suggestion): string {
  return `<article class="suggestion-row"><div><strong>${suggestionLabel(suggestion.kind)}</strong><span>${escapeHtml(suggestion.source)}</span></div><button data-action="apply-suggestion" data-id="${suggestion.id}" title="应用"><i data-lucide="check"></i></button><button data-action="reject-suggestion" data-id="${suggestion.id}" title="拒绝"><i data-lucide="x"></i></button></article>`;
}

function renderMilestoneSummary(milestones: ProjectMilestone[]): string {
  return `<div class="milestone-summary"><div class="milestone-summary-head"><span>状态</span><span>标题</span><span>权重</span><span>完成条件</span></div>${milestones.slice(0, 6).map((milestone) => `<div><span>${milestoneStatusLabel(milestone.status)}</span><strong>${escapeHtml(milestone.title)}</strong><span>${milestone.weight}</span><span>${escapeHtml(milestone.acceptanceCriteria || "未填写")}</span></div>`).join("") || `<div class="empty-row">尚未登记里程碑</div>`}</div>`;
}

function renderEmpty(): string { return `<div class="workspace-empty"><i data-lucide="target"></i><strong>选择一个项目</strong><span>查看 Todo、待确认事项、里程碑和工程活动</span></div>`; }
function fixedViewButton(id: FixedView, label: string, count: number, active: FixedView): string { return `<button data-action="fixed-view" data-view="${id}" class="${active === id ? "is-active" : ""}">${label}<small>${count}</small></button>`; }
function itemCounts(project: ProjectView): Record<FixedView, number> { return { todo: project.items.filter((item) => item.kind === "action" && item.status === "open").length, questions: project.items.filter((item) => item.kind === "question" && item.status === "open").length, blocking: project.items.filter((item) => item.blocking && item.status === "open").length, suggestions: project.items.filter((item) => item.suggestions.some((entry) => entry.status === "pending")).length, completed: project.items.filter((item) => item.status === "done").length }; }
function statusIcon(item: ItemView): string { if (item.status === "done") return `<i data-lucide="check-circle-2"></i>`; if (item.kind === "question") return `<i data-lucide="circle-help"></i>`; return `<i data-lucide="circle"></i>`; }
function milestoneName(project: ProjectView, id: string | null): string { return project.milestones.find((milestone) => milestone.id === id)?.title ?? "未分配"; }
function shortId(id: string): string { return id.replaceAll("-", "").slice(0, 5).toUpperCase(); }
function pageLabel(page: ProjectPage): string { return ({ overview: "概览", "work-items": "工作项", milestones: "里程碑", activity: "活动" } as Record<ProjectPage, string>)[page]; }
function statusLabel(status: string): string { return ({ active: "活跃", paused: "暂停", candidate: "候选", completed: "已完成", archived: "已归档" } as Record<string, string>)[status] ?? status; }
function maturityLabel(stage: MaturityStage): string { return ({ idea: "想法", defined: "已定义", executing: "执行中", validating: "验证中", mature: "成熟" } as Record<MaturityStage, string>)[stage]; }
function priorityLabel(priority: number): string { return priority >= 14 ? "高" : priority >= 7 ? "中" : "低"; }
function milestoneStatusLabel(status: MilestoneStatus): string { return ({ planned: "计划", in_progress: "进行中", completed: "完成", cancelled: "取消" } as Record<MilestoneStatus, string>)[status]; }
function suggestionLabel(kind: Suggestion["kind"]): string { return ({ item_patch: "事项更新", completion_candidate: "完成候选", target_resolution: "目标待确认", milestone_patch: "里程碑更新" } as Record<Suggestion["kind"], string>)[kind]; }
function dispositionLabel(value: string): string { return ({ applied: "已应用", suggested: "已建议", evidence_only: "仅证据", reverted: "已撤销" } as Record<string, string>)[value] ?? value; }
function eventLabel(value: string): string { return value === "work_item.completed" ? "工程事项完成事件" : value; }
function historyLabel(value: string): string { return ({ project_manual: "项目已修改", item_manual: "事项已修改", milestone_manual: "里程碑已修改", milestone_upsert: "里程碑已登记", event_apply: "工程事件已回写", event_revert: "工程事件已撤销", suggestion_decision: "自动建议已处理", quick_add: "事项已新增" } as Record<string, string>)[value] ?? value; }
function projectStatusOptions(selected: ProjectStatus): string { return (["candidate", "active", "paused", "completed", "archived"] as ProjectStatus[]).map((value) => `<option value="${value}" ${selected === value ? "selected" : ""}>${statusLabel(value)}</option>`).join(""); }
function maturityOptions(selected: MaturityStage): string { return (["idea", "defined", "executing", "validating", "mature"] as MaturityStage[]).map((value) => `<option value="${value}" ${selected === value ? "selected" : ""}>${maturityLabel(value)}</option>`).join(""); }
function milestoneStatusOptions(selected: MilestoneStatus): string { return (["planned", "in_progress", "completed", "cancelled"] as MilestoneStatus[]).map((value) => `<option value="${value}" ${selected === value ? "selected" : ""}>${milestoneStatusLabel(value)}</option>`).join(""); }
function formatTime(value: string): string { try { return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); } catch { return value; } }
function escapeHtml(value: string): string { return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
function escapeAttr(value: string): string { return escapeHtml(value); }
