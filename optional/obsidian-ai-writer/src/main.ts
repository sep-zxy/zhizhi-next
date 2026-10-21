import { MarkdownView, Notice, Plugin, TFile, type WorkspaceLeaf } from "obsidian";
import { AiWriterSettingTab } from "./settings";
import { AiWriterView, AI_WRITER_VIEW_TYPE } from "./ui/AiWriterView";
import { TemplateCreatorView, TEMPLATE_CREATOR_VIEW_TYPE } from "./ui/TemplateCreatorView";
import { createTransport } from "./core/transport";
import {
  DEFAULT_SETTINGS,
  type AiWriterPluginData,
  type AiWriterSettings,
  type WriterSession
} from "./core/types";
import { inferArticleTypeFromMarkdown } from "./core/articleTypes";
import { normalizeWriterSession } from "./core/session";

export default class AiWriterPlugin extends Plugin {
  settings: AiWriterSettings = structuredClone(DEFAULT_SETTINGS);
  sessions: Record<string, WriterSession> = {};
  private persistQueue: Promise<void> = Promise.resolve();

  async onload() {
    await this.loadSettings();
    this.registerView(AI_WRITER_VIEW_TYPE, (leaf) => new AiWriterView(leaf, this));
    this.registerView(TEMPLATE_CREATOR_VIEW_TYPE, (leaf) => new TemplateCreatorView(leaf, this));
    this.addSettingTab(new AiWriterSettingTab(this.app, this));

    this.registerEvent(this.app.vault.on("rename", (file, oldPath) => {
      if (file instanceof TFile && file.extension === "md") {
        void this.renameWriterPath(oldPath, file.path);
      }
    }));

    this.registerEvent(this.app.vault.on("modify", (file) => {
      if (!(file instanceof TFile) || file.extension !== "md") return;
      for (const leaf of this.app.workspace.getLeavesOfType(AI_WRITER_VIEW_TYPE)) {
        (leaf.view as AiWriterView).handleTargetFileModified(file);
      }
    }));

    this.registerEvent(this.app.workspace.on("file-open", (file) => {
      if (!file || file.extension !== "md") return;
      for (const leaf of this.app.workspace.getLeavesOfType(AI_WRITER_VIEW_TYPE)) {
        void (leaf.view as AiWriterView).syncTargetFile(file);
      }
    }));

    this.addRibbonIcon("pen-line", "打开 AI Writer", async () => {
      await this.openWriter();
    });

    this.addRibbonIcon("file-plus-2", "按模板新建", async () => {
      await this.openTemplateCreator();
    });

    this.addCommand({
      id: "open-ai-writer",
      name: "打开写作面板",
      callback: async () => await this.openWriter()
    });

    this.addCommand({
      id: "open-ai-writer-template-creator",
      name: "打开按模板新建页面",
      callback: async () => await this.openTemplateCreator()
    });

    this.addCommand({
      id: "add-current-note-to-ai-writer",
      name: "把当前笔记加入写作素材",
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (!file || file.extension !== "md") return false;
        if (!checking) void this.addFileToWriter(file);
        return true;
      }
    });

    this.addCommand({
      id: "add-selection-to-ai-writer",
      name: "把当前选区加入写作素材",
      editorCheckCallback: (checking, editor, view) => {
        const selection = editor.getSelection().trim();
        if (!selection) return false;
        if (!checking) {
          void this.addSelectionToWriter(selection, view.file?.basename || "当前选区", view.file);
        }
        return true;
      }
    });
  }

  onunload() {
    void this.persistData();
    this.app.workspace.detachLeavesOfType(AI_WRITER_VIEW_TYPE);
    this.app.workspace.detachLeavesOfType(TEMPLATE_CREATOR_VIEW_TYPE);
  }

  async loadSettings() {
    const raw = (await this.loadData()) as (Partial<AiWriterPluginData> & Partial<AiWriterSettings>) | null;
    const saved = raw?.settings || raw;
    this.settings = {
      ...structuredClone(DEFAULT_SETTINGS),
      ...(saved || {}),
      promptOverrides: {
        ...DEFAULT_SETTINGS.promptOverrides,
        ...(saved?.promptOverrides || {})
      },
      articleTypes: {
        blog: { ...DEFAULT_SETTINGS.articleTypes.blog, ...(saved?.articleTypes?.blog || {}) },
        "app-note": { ...DEFAULT_SETTINGS.articleTypes["app-note"], ...(saved?.articleTypes?.["app-note"] || {}) },
        software: { ...DEFAULT_SETTINGS.articleTypes.software, ...(saved?.articleTypes?.software || {}) }
      }
    };
    this.sessions = {};
    for (const [targetPath, value] of Object.entries(raw?.sessions || {})) {
      let articleType = value.articleType || "blog";
      const file = this.app.vault.getAbstractFileByPath(targetPath);
      if (!value.articleType && file instanceof TFile) {
        articleType = inferArticleTypeFromMarkdown(await this.app.vault.cachedRead(file));
      }
      this.sessions[targetPath] = normalizeWriterSession(targetPath, value, articleType);
    }
    if (raw && raw.schemaVersion !== 4) await this.persistData();
  }

  async saveSettings() {
    await this.persistData();
  }

  getWriterSession(targetPath: string) {
    return this.sessions[targetPath] ? structuredClone(this.sessions[targetPath]) : null;
  }

  async saveWriterSession(session: WriterSession) {
    this.sessions[session.targetPath] = normalizeWriterSession(session.targetPath, {
      ...structuredClone(session),
      updatedAt: new Date().toISOString()
    }, session.articleType);
    await this.persistData();
  }

  private async persistData() {
    const data: AiWriterPluginData = {
      schemaVersion: 4,
      settings: this.settings,
      sessions: this.sessions
    };
    this.persistQueue = this.persistQueue
      .catch(() => undefined)
      .then(async () => await this.saveData(data));
    await this.persistQueue;
  }

  private async renameWriterPath(oldPath: string, newPath: string) {
    for (const leaf of this.app.workspace.getLeavesOfType(AI_WRITER_VIEW_TYPE)) {
      (leaf.view as AiWriterView).handleFileRename(oldPath, newPath);
    }
    const session = this.sessions[oldPath];
    if (!session) return;
    const contextRefs = session.contextRefs.map((item) => item.path === oldPath ? { ...item, path: newPath } : item);
    delete this.sessions[oldPath];
    this.sessions[newPath] = normalizeWriterSession(newPath, { ...session, targetPath: newPath, contextRefs }, session.articleType);
    await this.persistData();
  }

  async testConnection() {
    const controller = new AbortController();
    await createTransport(this.settings).testConnection(controller.signal);
  }

  async openWriter() {
    let leaf = this.app.workspace.getLeavesOfType(AI_WRITER_VIEW_TYPE)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false) || this.app.workspace.getLeaf("tab");
      await leaf.setViewState({ type: AI_WRITER_VIEW_TYPE, active: true });
    }
    const activeFile = this.app.workspace.getActiveFile();
    if (activeFile?.extension === "md") {
      await (leaf.view as AiWriterView).syncTargetFile(activeFile);
    }
    await this.app.workspace.revealLeaf(leaf);
    return leaf.view as AiWriterView;
  }

  getActiveFolderPath() {
    const activeFile = this.app.workspace.getActiveFile();
    return activeFile?.parent?.path || "";
  }

  async openTemplateCreator() {
    const targetFolder = this.getActiveFolderPath();
    let leaf = this.app.workspace.getLeavesOfType(TEMPLATE_CREATOR_VIEW_TYPE)[0];
    if (!leaf) {
      leaf = this.app.workspace.getLeaf("tab");
      await leaf.setViewState({ type: TEMPLATE_CREATOR_VIEW_TYPE, active: true });
    }
    (leaf.view as TemplateCreatorView).setTargetFolder(targetFolder);
    await this.app.workspace.revealLeaf(leaf);
  }

  private async addFileToWriter(file: TFile) {
    const view = await this.openWriter();
    await view.addFile(file, true);
    new Notice(`已加入素材：${file.path}`);
  }

  private async addSelectionToWriter(selection: string, sourceName: string, file: TFile | null) {
    const view = await this.openWriter();
    await view.addSelection(selection, `${sourceName} · 选区`, file);
    new Notice("已把当前选区加入 AI Writer。\n");
  }
}
