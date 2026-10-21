import type { EditorView, ItemKind, ItemView, ProjectView } from "./types";

export type ProjectPage = "overview" | "work-items" | "milestones" | "activity";
export type FixedView = "todo" | "questions" | "blocking" | "suggestions" | "completed";
export type LayoutMode = "list" | "board";
export type ProjectScope = "all" | "candidate";
export type PriorityFilter = "all" | "high" | "medium" | "low";

export interface WorkItemFilters {
  kind: "all" | ItemKind;
  milestoneId: "all" | "unassigned" | string;
  priority: PriorityFilter;
}

export class EditorState {
  selectedProjectId = "";
  selectedItemId = "";
  page: ProjectPage = "work-items";
  fixedView: FixedView = "todo";
  layout: LayoutMode = "list";
  projectScope: ProjectScope = "all";
  projectSearch = "";
  filterOpen = false;
  filters: WorkItemFilters = { kind: "all", milestoneId: "all", priority: "all" };
  draftDirty = false;
  conflictRevision: number | null = null;

  reconcile(data: EditorView, keepSelection: boolean): void {
    if (!keepSelection || !data.projects.some((project) => project.id === this.selectedProjectId)) {
      this.selectedProjectId = data.projects.find((project) => project.status === "active")?.id ?? data.projects[0]?.id ?? "";
    }
    const project = this.project(data);
    if (!project?.items.some((item) => item.id === this.selectedItemId)) this.selectedItemId = "";
    this.conflictRevision = null;
  }

  project(data: EditorView | null): ProjectView | null {
    return data?.projects.find((project) => project.id === this.selectedProjectId) ?? null;
  }

  item(data: EditorView | null): ItemView | null {
    return this.project(data)?.items.find((item) => item.id === this.selectedItemId) ?? null;
  }

  visibleItems(project: ProjectView): ItemView[] {
    let items = fixedViewItems(project, this.fixedView);
    if (this.filters.kind !== "all") items = items.filter((item) => item.kind === this.filters.kind);
    if (this.filters.milestoneId === "unassigned") items = items.filter((item) => item.milestoneId === null);
    else if (this.filters.milestoneId !== "all") items = items.filter((item) => item.milestoneId === this.filters.milestoneId);
    if (this.filters.priority === "high") items = items.filter((item) => item.priority >= 14);
    else if (this.filters.priority === "medium") items = items.filter((item) => item.priority >= 7 && item.priority < 14);
    else if (this.filters.priority === "low") items = items.filter((item) => item.priority < 7);
    return items;
  }

  hasFilters(): boolean {
    return this.filters.kind !== "all" || this.filters.milestoneId !== "all" || this.filters.priority !== "all";
  }

  noteExternalRevision(revision: number): boolean {
    if (!this.draftDirty) return false;
    this.conflictRevision = revision;
    return true;
  }
}

export function fixedViewItems(project: ProjectView, view: FixedView): ItemView[] {
  if (view === "todo") return project.items.filter((item) => item.kind === "action" && item.status === "open");
  if (view === "questions") return project.items.filter((item) => item.kind === "question" && item.status === "open");
  if (view === "blocking") return project.items.filter((item) => item.blocking && item.status === "open");
  if (view === "suggestions") return project.items.filter((item) => item.suggestions.some((entry) => entry.status === "pending"));
  return project.items.filter((item) => item.status === "done");
}

export function boardGroups(items: ItemView[]): Array<{ id: string; label: string; items: ItemView[] }> {
  return [
    { id: "blocking", label: "阻塞", items: items.filter((item) => item.status === "open" && item.blocking) },
    { id: "questions", label: "待确认", items: items.filter((item) => item.status === "open" && !item.blocking && item.kind === "question") },
    { id: "todo", label: "Todo", items: items.filter((item) => item.status === "open" && !item.blocking && item.kind === "action") },
    { id: "completed", label: "已完成", items: items.filter((item) => item.status === "done") },
  ];
}
