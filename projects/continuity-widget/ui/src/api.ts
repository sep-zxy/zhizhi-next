import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { fixtureDashboardView, fixtureEditorView } from "./fixtures";
import type {
  DashboardView, EditorView, Item, ItemHorizon, ItemKind, ItemStatus, MilestoneStatus,
  MaturityStage, MutationResult, Project, ProjectMilestone, ProjectPreview, ProjectStatus,
  RevisionUpdate, Settings, SnapResult, Suggestion,
} from "./types";

const fixtureMode = new URLSearchParams(window.location.search).has("fixture");
const readonlyFixture = (): Promise<never> => Promise.reject(new Error("fixture_readonly"));

export const api = {
  dashboard: (): Promise<DashboardView> => fixtureMode ? Promise.resolve(structuredClone(fixtureDashboardView)) : invoke("get_dashboard"),
  editor: (): Promise<EditorView> => fixtureMode ? Promise.resolve(structuredClone(fixtureEditorView)) : invoke("get_editor"),
  preview: (projectId: string): Promise<ProjectPreview> => fixtureMode ? Promise.resolve({ revision: fixtureEditorView.revision, project: structuredClone(fixtureEditorView.projects.find((project) => project.id === projectId) ?? fixtureEditorView.projects[0]!) }) : invoke("get_project_preview", { projectId }),
  poll: (sinceRevision: number): Promise<RevisionUpdate> => fixtureMode ? Promise.resolve({ revision: sinceRevision, changed: false, dashboard: null }) : invoke("revision_poll", { sinceRevision }),
  quickAdd: (title: string, projectId: string | null, expectedRevision: number): Promise<MutationResult<Item>> =>
    fixtureMode ? readonlyFixture() : invoke("quick_add", { input: { title, projectId, expectedRevision } }),
  completeItem: (id: string, expectedRevision: number): Promise<MutationResult<Item>> =>
    invoke("complete_item", { input: { id, expectedRevision } }),
  confirmProject: (id: string, expectedRevision: number): Promise<MutationResult<Project>> =>
    invoke("confirm_project", { input: { id, expectedRevision } }),
  saveProject: (input: {
    id: string; name?: string; summary?: string; goal?: string; successCriteria?: string;
    maturityStage?: MaturityStage; status?: ProjectStatus; projectPath?: string | null;
    thresholdOverride?: number | null; expectedRevision: number;
  }): Promise<MutationResult<Project>> => invoke("save_project", { input }),
  saveItem: (input: {
    id: string; title?: string; details?: string; kind?: ItemKind; horizon?: ItemHorizon;
    status?: ItemStatus; priority?: number; pinned?: boolean; milestoneId?: string | null;
    blocking?: boolean; sortOrder?: number; expectedRevision: number;
  }): Promise<MutationResult<Item>> => invoke("save_item", { input }),
  upsertMilestone: (input: {
    id?: string; projectId: string; fingerprint: string; title: string; acceptanceCriteria?: string;
    status?: MilestoneStatus; weight?: number; sortOrder?: number; expectedRevision: number;
  }): Promise<MutationResult<ProjectMilestone>> => invoke("upsert_milestone", { input }),
  saveMilestone: (input: {
    id: string; title?: string; acceptanceCriteria?: string; status?: MilestoneStatus;
    weight?: number; sortOrder?: number; expectedRevision: number;
  }): Promise<MutationResult<ProjectMilestone>> => invoke("save_milestone", { input }),
  applySuggestion: (id: string, expectedRevision: number): Promise<MutationResult<Suggestion>> =>
    invoke("apply_suggestion", { input: { id, expectedRevision } }),
  rejectSuggestion: (id: string, expectedRevision: number): Promise<MutationResult<Suggestion>> =>
    invoke("reject_suggestion", { input: { id, expectedRevision } }),
  saveSettings: (input: Partial<Pick<Settings, "dailyThreshold" | "nowLimit" | "alwaysOnTop" | "autoSnap" | "autostart">> & { expectedRevision: number }): Promise<MutationResult<Settings>> =>
    invoke("save_settings", { input }),
  openEditor: (): Promise<void> => invoke("open_editor"),
  openWidget: (): Promise<void> => invoke("open_widget"),
  showPreview: (projectId: string): Promise<void> => invoke("show_preview", { projectId }),
  hidePreview: (): Promise<void> => invoke("hide_preview"),
  openProjectPath: (projectId: string): Promise<void> => invoke("open_project_path", { projectId }),
  snap: (forcedEdge: string | null): Promise<SnapResult | null> => invoke("snap_window", { forcedEdge }),
  heightMode: (mode: "fit" | "full", desiredHeight?: number): Promise<void> => invoke("set_height_mode", { mode, desiredHeight }),
  captureWindowState: (): Promise<void> => invoke("capture_window_state"),
  quit: (): Promise<void> => invoke("quit_app"),
};

export function onRevision(callback: (revision: number) => void): Promise<UnlistenFn> {
  if (fixtureMode) return Promise.resolve(() => undefined);
  return listen<number>("continuity://revision", (event) => callback(event.payload));
}

export function startRevisionPolling(getRevision: () => number, onChange: (update: RevisionUpdate) => void): () => void {
  if (fixtureMode) return () => undefined;
  let running = false;
  const timer = window.setInterval(async () => {
    if (running) return;
    running = true;
    try {
      const update = await api.poll(getRevision());
      if (update.changed) onChange(update);
    } finally {
      running = false;
    }
  }, 1000);
  return () => window.clearInterval(timer);
}
