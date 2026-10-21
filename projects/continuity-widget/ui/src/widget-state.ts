import type { DashboardView, ItemView, ProjectDashboardView } from "./types";

export class WidgetState {
  selectedProjectId = "";

  reconcile(data: DashboardView): void {
    if (!data.projects.some((entry) => entry.project.id === this.selectedProjectId)) {
      this.selectedProjectId = data.projects.find((entry) => entry.project.status === "active")?.project.id ?? data.projects[0]?.project.id ?? "";
    }
  }

  project(data: DashboardView): ProjectDashboardView | null {
    return data.projects.find((entry) => entry.project.id === this.selectedProjectId) ?? null;
  }

  actions(data: DashboardView): ItemView[] {
    const selected = this.project(data);
    return (selected?.nextItems ?? []).filter((item) => item.kind === "action" && item.status === "open").slice(0, data.settings.nowLimit);
  }

  questions(data: DashboardView): ItemView[] {
    const selected = this.project(data);
    return (selected?.nextItems ?? []).filter((item) => item.kind === "question" && item.status === "open").slice(0, 4);
  }
}
