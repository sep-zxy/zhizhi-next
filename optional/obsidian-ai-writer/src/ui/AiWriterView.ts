import {
  App,
  DropdownComponent,
  FuzzySuggestModal,
  ItemView,
  MarkdownRenderer,
  MarkdownView,
  Notice,
  setIcon,
  TFile,
  TextAreaComponent,
  type WorkspaceLeaf
} from "obsidian";
import type AiWriterPlugin from "../main";
import {
  EMPTY_OUTPUTS,
  EMPTY_FEEDBACK,
  type ArticleTypeId,
  type ArticleStyleId,
  type ContextItem,
  type ImagePlaceholder,
  type StageFeedback,
  type WriterPreviewTab,
  type WriterRevision,
  type WorkflowOutputs,
  type WorkflowStage,
  type WriterSession
} from "../core/types";
import { assembleContext, createContextItem, upsertContextItem } from "../core/context";
import { createTransport } from "../core/transport";
import { runWorkflowStage } from "../core/workflow";
import {
  createArticleDiff,
  createTextReplacement,
  countReadableCharacters,
  extractManagedArticle,
  extractMarkdownBody,
  isLikelySameArticle,
  isWithinTargetLength,
  replaceManagedArticle
} from "../core/article";
import { compilePersonalStylePrompt } from "../core/prompts";
import { KHAZIX_SYSTEM_PROMPT } from "../style/khazixStylePack";
import { lintKhazixArticle } from "../style/styleLint";
import { calculateBottomOverlayOffset } from "../core/layout";
import { inferArticleTypeFromMarkdown } from "../core/articleTypes";
import {
  ARTICLE_STYLE_IDS,
  defaultStyleForArticleType,
  getArticleStyle
} from "../core/articleStyles";
import {
  appendRevision,
  contextItemsToRefs,
  getStageOutput,
  restoreStageRevision
} from "../core/session";
import { materializeImagePlaceholders } from "../core/illustrations";
import { lintMarkdownStructure } from "../core/markdownQuality";
import { removeMeaninglessMarkdownMarkers } from "../core/outputValidation";

export const AI_WRITER_VIEW_TYPE = "ai-writer-view";

const STAGES: Array<{ id: WorkflowStage; label: string; short: string }> = [
  { id: "outline", label: "选题与提纲", short: "提纲" },
  { id: "draft", label: "完整初稿", short: "初稿" },
  { id: "review", label: "四层审校", short: "审校" },
  { id: "images", label: "插入占位图", short: "插图" }
];

export class AiWriterView extends ItemView {
  private contextItems: ContextItem[] = [];
  private targetPath = "";
  private articleType: ArticleTypeId = "blog";
  private articleStyle: ArticleStyleId = "blog";
  private stylePrompt = getArticleStyle("blog").prompt;
  private materialsCollapsed = false;
  private brief = "";
  private targetLength = 4000;
  private originalOutline = "";
  private outputs: WorkflowOutputs = structuredClone(EMPTY_OUTPUTS);
  private imagePlaceholders: ImagePlaceholder[] = [];
  private feedback: StageFeedback = structuredClone(EMPTY_FEEDBACK);
  private history: WriterRevision[] = [];
  private runningStage: WorkflowStage | "style" | null = null;
  private runningText = "";
  private errorMessage = "";
  private controller: AbortController | null = null;
  private activeStage: WorkflowStage = "outline";
  private previewTab: WriterPreviewTab = "article";
  private editingStage: WorkflowStage | null = null;
  private promptExpanded = false;
  private expandedImagePrompts = new Set<string>();
  private liveOutputEl: HTMLElement | null = null;
  private statusBarObserver: ResizeObserver | null = null;
  private persistTimer: number | null = null;
  private fileSyncTimer: number | null = null;
  private pendingOwnWrite: { path: string; markdown: string } | null = null;

  constructor(leaf: WorkspaceLeaf, private readonly plugin: AiWriterPlugin) {
    super(leaf);
  }

  getViewType() {
    return AI_WRITER_VIEW_TYPE;
  }

  getDisplayText() {
    return "AI Writer";
  }

  getIcon() {
    return "pen-line";
  }

  async onOpen() {
    const activeFile = this.app.workspace.getActiveFile();
    if (activeFile?.extension === "md") {
      await this.setTargetFile(activeFile);
    }
    this.render();
    this.installStatusBarTracking();
  }

  async onClose() {
    this.controller?.abort();
    if (this.persistTimer !== null) window.clearTimeout(this.persistTimer);
    if (this.fileSyncTimer !== null) window.clearTimeout(this.fileSyncTimer);
    await this.persistSession();
    this.statusBarObserver?.disconnect();
    this.statusBarObserver = null;
    this.contentEl.empty();
  }

  async addFile(file: TFile, setAsTarget = false) {
    if (file.extension !== "md") {
      new Notice("AI Writer 测试版只读取 Markdown 笔记。\n");
      return;
    }

    const content = await this.app.vault.cachedRead(file);
    if (setAsTarget) {
      await this.setTargetFile(file);
    }
    this.contextItems = upsertContextItem(
      this.contextItems,
      createContextItem("note", file.basename, content, file.path)
    );
    this.schedulePersistSession();
    this.render();
  }

  async addSelection(content: string, label: string, file?: TFile | null) {
    if (file) {
      await this.setTargetFile(file);
    }
    this.contextItems = upsertContextItem(
      this.contextItems,
      createContextItem("selection", label, content)
    );
    this.schedulePersistSession();
    this.render();
  }

  async syncTargetFile(file: TFile) {
    await this.setTargetFile(file);
    await this.syncFromTargetFile({ force: false, render: false });
    this.render();
  }

  handleTargetFileModified(file: TFile) {
    if (file.path !== this.targetPath) return;
    if (this.fileSyncTimer !== null) window.clearTimeout(this.fileSyncTimer);
    this.fileSyncTimer = window.setTimeout(() => {
      this.fileSyncTimer = null;
      void this.handleDebouncedTargetFileModified(file);
    }, 450);
  }

  handleFileRename(oldPath: string, newPath: string) {
    if (this.targetPath === oldPath) this.targetPath = newPath;
    this.contextItems = this.contextItems.map((item) => item.path === oldPath ? { ...item, path: newPath } : item);
    this.schedulePersistSession();
    this.render();
  }

  private async setTargetFile(file: TFile) {
    if (this.targetPath === file.path) return;
    if (this.fileSyncTimer !== null) {
      window.clearTimeout(this.fileSyncTimer);
      this.fileSyncTimer = null;
    }
    if (this.persistTimer !== null) {
      window.clearTimeout(this.persistTimer);
      this.persistTimer = null;
    }
    if (this.targetPath) await this.persistSession();

    this.targetPath = file.path;
    const saved = this.plugin.getWriterSession(file.path);
    if (saved) {
      await this.restoreSession(saved);
    } else {
      const content = await this.app.vault.cachedRead(file);
      this.articleType = inferArticleTypeFromMarkdown(content);
      this.articleStyle = defaultStyleForArticleType(this.articleType);
      this.stylePrompt = getArticleStyle(this.articleStyle).prompt;
      this.contextItems = [];
      this.brief = "";
      this.targetLength = 4000;
      this.originalOutline = "";
      this.outputs = structuredClone(EMPTY_OUTPUTS);
      this.imagePlaceholders = [];
      this.feedback = structuredClone(EMPTY_FEEDBACK);
      this.history = [];
      this.activeStage = "outline";
      this.previewTab = "article";
      this.materialsCollapsed = false;
      this.promptExpanded = false;
      this.expandedImagePrompts.clear();
    }
  }

  private async handleDebouncedTargetFileModified(file: TFile) {
    if (file.path !== this.targetPath) return;
    const markdown = await this.readTargetMarkdown(file);
    if (this.pendingOwnWrite?.path === file.path && this.pendingOwnWrite.markdown === markdown) {
      this.pendingOwnWrite = null;
      return;
    }
    this.pendingOwnWrite = null;
    await this.syncFromTargetFile({ force: false, markdown });
  }

  private async syncFromTargetFile(options: {
    force: boolean;
    stage?: "draft" | "review" | "images";
    markdown?: string;
    announce?: boolean;
    render?: boolean;
  }) {
    const stage = options.stage || this.getWorkspaceSyncStage();
    if (!stage || !this.targetPath) return false;
    const target = this.app.vault.getAbstractFileByPath(this.targetPath);
    if (!(target instanceof TFile)) return false;

    const markdown = options.markdown ?? await this.readTargetMarkdown(target);
    const managed = extractManagedArticle(markdown);
    const incoming = (managed ?? extractMarkdownBody(markdown)).trim();
    const known = this.getStageArticle(stage).trim();
    if (!incoming || incoming === known) return false;
    if (!options.force && managed === null && (!known || !isLikelySameArticle(incoming, known))) return false;

    this.setStageArticle(stage, incoming);
    await this.persistSession();
    if (options.render !== false) this.render();
    if (options.announce) new Notice(`已从工作区同步到${stageLabel(stage)}。`);
    return true;
  }

  private getWorkspaceSyncStage(): "draft" | "review" | "images" | null {
    if (this.outputs.illustrated) return "images";
    if (this.outputs.review) return "review";
    if (this.outputs.draft) return "draft";
    return null;
  }

  private getStageArticle(stage: "draft" | "review" | "images") {
    return stage === "images" ? this.outputs.illustrated : this.outputs[stage];
  }

  private setStageArticle(stage: "draft" | "review" | "images", value: string) {
    if (stage === "images") this.outputs.illustrated = value;
    else {
      this.outputs[stage] = value;
      this.clearDownstreamOutputs(stage);
    }
  }

  private clearDownstreamOutputs(stage: Exclude<WorkflowStage, "images">) {
    if (stage === "outline") this.outputs.draft = "";
    if (stage === "outline" || stage === "draft") this.outputs.review = "";
    this.outputs.images = "";
    this.outputs.illustrated = "";
    this.imagePlaceholders = [];
  }

  private getOpenTargetView() {
    for (const leaf of this.app.workspace.getLeavesOfType("markdown")) {
      if (leaf.view instanceof MarkdownView && leaf.view.file?.path === this.targetPath) return leaf.view;
    }
    return null;
  }

  private async readTargetMarkdown(file: TFile) {
    const view = this.getOpenTargetView();
    return view?.file?.path === file.path ? view.editor.getValue() : await this.app.vault.read(file);
  }

  private async restoreSession(session: WriterSession) {
    this.articleType = session.articleType;
    this.articleStyle = session.articleStyle;
    this.stylePrompt = session.stylePrompt || getArticleStyle(session.articleStyle).prompt;
    this.brief = session.brief;
    this.targetLength = session.targetLength;
    this.originalOutline = session.originalOutline;
    this.outputs = {
      ...structuredClone(session.outputs),
      outline: removeMeaninglessMarkdownMarkers(session.outputs.outline),
      draft: removeMeaninglessMarkdownMarkers(session.outputs.draft),
      review: removeMeaninglessMarkdownMarkers(session.outputs.review),
      images: removeMeaninglessMarkdownMarkers(session.outputs.images),
      illustrated: removeMeaninglessMarkdownMarkers(session.outputs.illustrated)
    };
    this.imagePlaceholders = structuredClone(session.imagePlaceholders);
    this.feedback = { ...structuredClone(EMPTY_FEEDBACK), ...structuredClone(session.feedback) };
    this.history = structuredClone(session.history);
    this.activeStage = session.activeStage;
    this.previewTab = normalizePreviewTab(this.activeStage, session.previewTab, this.imagePlaceholders.length > 0);
    this.contextItems = await this.restoreContextItems(session);
    this.materialsCollapsed = Boolean(session.outputs.outline);
    this.promptExpanded = false;
    this.expandedImagePrompts = new Set(this.imagePlaceholders[0] ? [this.imagePlaceholders[0].id] : []);
  }

  private async persistSession() {
    if (!this.targetPath) return;
    await this.plugin.saveWriterSession({
      targetPath: this.targetPath,
      articleType: this.articleType,
      articleStyle: this.articleStyle,
      stylePrompt: this.stylePrompt,
      brief: this.brief,
      targetLength: this.targetLength,
      contextRefs: contextItemsToRefs(this.contextItems),
      originalOutline: this.originalOutline,
      outputs: structuredClone(this.outputs),
      imagePlaceholders: structuredClone(this.imagePlaceholders),
      feedback: structuredClone(this.feedback),
      activeStage: this.activeStage,
      previewTab: this.previewTab,
      history: structuredClone(this.history),
      updatedAt: new Date().toISOString()
    });
  }

  private schedulePersistSession() {
    if (this.persistTimer !== null) window.clearTimeout(this.persistTimer);
    this.persistTimer = window.setTimeout(() => {
      this.persistTimer = null;
      void this.persistSession();
    }, 500);
  }

  private async restoreContextItems(session: WriterSession) {
    const restored: ContextItem[] = [];
    for (const ref of session.contextRefs) {
      if (ref.kind === "note" && ref.path) {
        const file = this.app.vault.getAbstractFileByPath(ref.path);
        if (file instanceof TFile) {
          const item = createContextItem("note", ref.label, await this.app.vault.cachedRead(file), ref.path);
          restored.push({ ...item, id: ref.id });
        }
      } else if (ref.kind === "selection" && ref.content) {
        const item = createContextItem("selection", ref.label, ref.content, ref.path);
        restored.push({ ...item, id: ref.id });
      }
    }
    return restored;
  }

  private buildSession(): WriterSession {
    return {
      targetPath: this.targetPath,
      articleType: this.articleType,
      articleStyle: this.articleStyle,
      stylePrompt: this.stylePrompt,
      brief: this.brief,
      targetLength: this.targetLength,
      contextRefs: contextItemsToRefs(this.contextItems),
      originalOutline: this.originalOutline,
      outputs: structuredClone(this.outputs),
      imagePlaceholders: structuredClone(this.imagePlaceholders),
      feedback: structuredClone(this.feedback),
      activeStage: this.activeStage,
      previewTab: this.previewTab,
      history: structuredClone(this.history),
      updatedAt: new Date().toISOString()
    };
  }

  private recordRevision(reason: string, stage?: WorkflowStage) {
    const next = appendRevision(this.buildSession(), reason, stage);
    this.history = next.history;
  }

  private render() {
    const root = this.contentEl;
    root.empty();
    root.addClass("aiw-view");

    const shell = root.createDiv({ cls: "aiw-shell" });
    this.renderHeader(shell);
    this.renderStageRail(shell);
    this.renderMaterials(shell);
    this.renderWorkspace(shell);
    this.renderFooter(root);
  }

  private installStatusBarTracking() {
    this.updateStatusBarOffset();
    this.registerDomEvent(window, "resize", this.updateStatusBarOffset);
    this.registerEvent(this.app.workspace.on("layout-change", this.updateStatusBarOffset));

    const statusBar = document.querySelector<HTMLElement>(".status-bar");
    if (statusBar && typeof ResizeObserver !== "undefined") {
      this.statusBarObserver = new ResizeObserver(this.updateStatusBarOffset);
      this.statusBarObserver.observe(statusBar);
    }
  }

  private updateStatusBarOffset = () => {
    const statusBar = document.querySelector<HTMLElement>(".status-bar");
    if (!statusBar || getComputedStyle(statusBar).display === "none") {
      this.contentEl.style.setProperty("--aiw-bottom-offset", "0px");
      return;
    }

    const offset = calculateBottomOverlayOffset(
      this.contentEl.getBoundingClientRect(),
      statusBar.getBoundingClientRect()
    );
    this.contentEl.style.setProperty("--aiw-bottom-offset", `${offset}px`);
  };

  private renderHeader(root: HTMLElement) {
    const header = root.createDiv({ cls: "aiw-header" });
    const identity = header.createDiv({ cls: "aiw-identity" });
    const mark = identity.createDiv({ cls: "aiw-mark", attr: { "aria-hidden": "true" } });
    setIcon(mark, "pen-line");
    const titles = identity.createDiv();
    titles.createEl("h2", { text: "AI Writer" });
    titles.createEl("p", { text: "从素材到成稿", cls: "aiw-kicker" });

    const actions = header.createDiv({ cls: "aiw-header-actions" });
    this.createIconButton(actions, "按模板新建笔记", "file-plus-2", () => this.plugin.openTemplateCreator());
    const badge = actions.createDiv({
      cls: `aiw-transport-badge is-${this.plugin.settings.transportMode}`,
      text: this.plugin.settings.transportMode === "stream" ? "流式" : "兼容"
    });
    badge.setAttr(
      "title",
      this.plugin.settings.transportMode === "stream"
        ? "真实流式模式，需要 API 支持 CORS"
        : "非流式兼容模式，完整响应后显示"
    );
  }

  private renderStageRail(root: HTMLElement) {
    const current = this.getCurrentStage();
    const rail = root.createDiv({
      cls: "aiw-stage-rail",
      attr: { "aria-label": "写作阶段", role: "tablist" }
    });

    STAGES.forEach((stage, index) => {
      const completed = Boolean(getStageOutput(this.outputs, stage.id));
      const available = completed || current === stage.id;
      const selected = this.activeStage === stage.id;
      const item = rail.createEl("button", {
        cls: [
          "aiw-stage",
          completed ? "is-complete" : "",
          current === stage.id ? "is-current" : "",
          selected ? "is-selected" : "",
          this.runningStage === stage.id ? "is-running" : ""
        ].filter(Boolean).join(" "),
        attr: {
          role: "tab",
          "aria-selected": String(selected),
          "aria-controls": "aiw-stage-workspace"
        }
      });
      item.disabled = !available || Boolean(this.runningStage);
      const indexEl = item.createSpan({ text: completed ? "" : String(index + 1), cls: "aiw-stage-index" });
      if (completed) setIcon(indexEl, "check");
      item.createSpan({ text: stage.short, cls: "aiw-stage-label" });
      item.setAttr("title", available ? `查看${stage.label}` : `完成前一阶段后可用`);
      item.onclick = () => {
        this.activeStage = stage.id;
        this.previewTab = defaultPreviewTab(stage.id, this.imagePlaceholders.length > 0);
        this.editingStage = null;
        this.schedulePersistSession();
        this.render();
      };
    });
  }

  private renderMaterials(root: HTMLElement) {
    const section = this.createSection(root, "写作素材", "只有这里列出的内容会进入上下文");
    if (this.outputs.outline && this.materialsCollapsed) {
      const compact = section.createDiv({ cls: "aiw-materials-compact" });
      const icon = compact.createSpan({ cls: "aiw-target-icon" });
      setIcon(icon, "file-pen-line");
      const copy = compact.createDiv({ cls: "aiw-context-copy" });
      copy.createDiv({ text: this.targetPath || "未指定原文", cls: "aiw-context-title" });
      copy.createDiv({
        text: `${this.contextItems.length} 份素材 · 目标 ${formatCount(this.targetLength)} 字`,
        cls: "aiw-context-meta"
      });
      this.createButton(compact, "展开素材", "chevron-down", () => {
        this.materialsCollapsed = false;
        this.render();
      }, "aiw-compact-button");
      return;
    }

    if (this.outputs.outline) {
      this.createButton(section, "收起素材", "chevron-up", () => {
        this.materialsCollapsed = true;
        this.render();
      }, "aiw-link-button aiw-collapse-materials");
    }
    const target = section.createDiv({ cls: `aiw-target ${this.targetPath ? "is-ready" : ""}` });
    const targetIcon = target.createSpan({ cls: "aiw-target-icon" });
    setIcon(targetIcon, this.targetPath ? "file-pen-line" : "circle-alert");
    const targetCopy = target.createDiv();
    targetCopy.createDiv({ text: this.targetPath ? "最终写回原文" : "尚未指定写回原文", cls: "aiw-target-label" });
    targetCopy.createDiv({
      text: this.targetPath || "点击“当前笔记”，生成完成后将直接修改这篇笔记。",
      cls: "aiw-target-path"
    });
    const toolbar = section.createDiv({ cls: "aiw-toolbar" });

    this.createButton(toolbar, "当前笔记", "file-text", async () => {
      const file = this.app.workspace.getActiveFile();
      if (!file) {
        new Notice("先打开一篇 Markdown 笔记。\n");
        return;
      }
      await this.addFile(file, true);
    });

    this.createButton(toolbar, "当前选区", "text-select", () => {
      const markdownView = this.app.workspace.getActiveViewOfType(MarkdownView);
      const selection = markdownView?.editor.getSelection().trim();
      if (!markdownView || !selection) {
        new Notice("当前编辑器没有选中文本。\n");
        return;
      }
      void this.addSelection(selection, `${markdownView.file?.basename || "当前笔记"} · 选区`, markdownView.file);
    });

    this.createButton(toolbar, "选择笔记", "files", () => {
      new MarkdownFileSuggest(this.app, this.app.vault.getMarkdownFiles(), (file) => void this.addFile(file)).open();
    });

    this.renderWritingControls(section);

    const list = section.createDiv({ cls: "aiw-context-list" });
    if (this.contextItems.length === 0) {
      const empty = list.createDiv({ cls: "aiw-empty-inline" });
      setIcon(empty.createSpan(), "inbox");
      empty.createSpan({ text: "尚未加入素材" });
    } else {
      this.contextItems.forEach((item) => {
        const row = list.createDiv({ cls: "aiw-context-row" });
        const icon = row.createSpan({ cls: "aiw-context-icon" });
        setIcon(icon, item.kind === "selection" ? "text-select" : "file-text");
        const copy = row.createDiv({ cls: "aiw-context-copy" });
        copy.createDiv({ text: item.label, cls: "aiw-context-title" });
        copy.createDiv({
          text: `${item.path || "临时选区"} · ${formatCount(item.charCount)} 字符`,
          cls: "aiw-context-meta"
        });
        this.createIconButton(row, "移除素材", "x", () => {
          this.contextItems = this.contextItems.filter((candidate) => candidate.id !== item.id);
          this.schedulePersistSession();
          this.render();
        });
      });
    }

    if (this.contextItems.length > 0) {
      const snapshot = assembleContext(this.contextItems);
      const details = section.createEl("details", { cls: "aiw-context-preview" });
      details.createEl("summary", {
        text: `发送内容预览 · ${formatCount(snapshot.charCount)} 字符${snapshot.truncated ? " · 已截断" : ""}`
      });
      details.createEl("pre", { text: snapshot.text });

      this.createButton(section, "从这些样本分析个人偏好", "scan-text", () => void this.analyzePersonalStyle(), "aiw-link-button");
    }

    const briefGrid = section.createDiv({ cls: "aiw-brief-grid" });
    if (this.articleStyle === "custom") {
      const briefField = briefGrid.createDiv({ cls: "aiw-field aiw-brief-field" });
      briefField.createEl("label", { text: "这篇文章想讲什么" });
      const briefInput = briefField.createEl("textarea", {
        attr: { placeholder: "写下核心观点、目标读者、重点和素材取舍。" }
      });
      briefInput.value = this.brief;
      briefInput.oninput = () => {
        this.brief = briefInput.value;
        this.schedulePersistSession();
      };
      briefInput.onchange = () => void this.persistSession();
    }

    const lengthField = briefGrid.createDiv({ cls: "aiw-field aiw-length-field" });
    lengthField.createEl("label", { text: "目标成稿字数" });
    const lengthInput = lengthField.createEl("input", {
      type: "number",
      value: String(this.targetLength),
      attr: { min: "500", max: "20000", step: "100", inputmode: "numeric" }
    });
    lengthInput.oninput = () => {
      const parsed = Number.parseInt(lengthInput.value, 10);
      if (Number.isFinite(parsed)) this.targetLength = Math.min(20000, Math.max(500, parsed));
      this.schedulePersistSession();
    };
    lengthInput.onchange = () => void this.persistSession();
  }

  private renderWritingControls(section: HTMLElement) {
    const controls = section.createDiv({ cls: "aiw-writing-controls" });

    const templateField = controls.createDiv({ cls: "aiw-field" });
    templateField.createEl("label", { text: "模板类型" });
    const templateSelect = new DropdownComponent(templateField);
    (["blog", "app-note", "software"] as ArticleTypeId[]).forEach((id) => {
      templateSelect.addOption(id, this.plugin.settings.articleTypes[id].label);
    });
    templateSelect.setValue(this.articleType).onChange((value) => {
      const nextType = value as ArticleTypeId;
      const followsTemplate = this.articleStyle === defaultStyleForArticleType(this.articleType);
      this.articleType = nextType;
      if (followsTemplate) {
        this.articleStyle = defaultStyleForArticleType(nextType);
        this.stylePrompt = getArticleStyle(this.articleStyle).prompt;
      }
      this.schedulePersistSession();
      this.render();
    });
    templateField.createDiv({
      cls: "aiw-field-description",
      text: `由 frontmatter 的 section 自动识别，当前使用 ${this.plugin.settings.articleTypes[this.articleType].templatePath}`
    });

    const styleField = controls.createDiv({ cls: "aiw-field" });
    styleField.createEl("label", { text: "文章风格" });
    const styleSelect = new DropdownComponent(styleField);
    ARTICLE_STYLE_IDS.forEach((id) => styleSelect.addOption(id, getArticleStyle(id).label));
    styleSelect.setValue(this.articleStyle).onChange((value) => {
      this.articleStyle = value as ArticleStyleId;
      this.stylePrompt = getArticleStyle(this.articleStyle).prompt;
      this.schedulePersistSession();
      this.render();
    });
    styleField.createDiv({ cls: "aiw-field-description", text: getArticleStyle(this.articleStyle).description });

    const promptField = section.createEl("details", { cls: "aiw-style-prompt" });
    promptField.open = this.promptExpanded || this.articleStyle === "custom";
    promptField.ontoggle = () => {
      this.promptExpanded = promptField.open;
    };
    const promptSummary = promptField.createEl("summary", { cls: "aiw-style-prompt-summary" });
    const summaryIcon = promptSummary.createSpan({ cls: "aiw-context-icon", attr: { "aria-hidden": "true" } });
    setIcon(summaryIcon, "square-code");
    const summaryCopy = promptSummary.createDiv({ cls: "aiw-context-copy" });
    summaryCopy.createDiv({ text: "编辑文章提示词", cls: "aiw-context-title" });
    summaryCopy.createDiv({
      text: this.stylePrompt === getArticleStyle(this.articleStyle).prompt ? "当前使用默认结构规则" : "已修改，将覆盖默认结构规则",
      cls: "aiw-context-meta"
    });
    const promptBody = promptField.createDiv({ cls: "aiw-style-prompt-body" });
    const promptLabel = promptBody.createDiv({ cls: "aiw-field-label-row" });
    promptLabel.createEl("label", { text: "文章提示词代码块" });
    this.createButton(promptLabel, "恢复默认", "rotate-ccw", () => {
      this.stylePrompt = getArticleStyle(this.articleStyle).prompt;
      this.promptExpanded = true;
      this.schedulePersistSession();
      this.render();
    }, "aiw-link-button");
    const promptInput = new TextAreaComponent(promptBody);
    promptInput.inputEl.addClass("aiw-prompt-code");
    promptInput.inputEl.setAttr("spellcheck", "false");
    promptInput.inputEl.setAttr("aria-label", "可编辑文章提示词代码块");
    promptInput
      .setPlaceholder("规定文章结构、语气、开头和结尾方式。")
      .setValue(this.stylePrompt)
      .onChange((value) => {
        this.stylePrompt = value;
        this.schedulePersistSession();
      });
    promptInput.inputEl.addEventListener("change", () => void this.persistSession());
  }

  private renderWorkspace(root: HTMLElement) {
    const section = this.createSection(root, "稿件", this.getWorkspaceSubtitle());
    section.id = "aiw-stage-workspace";
    section.setAttr("role", "tabpanel");

    if (this.errorMessage) {
      const error = section.createDiv({ cls: "aiw-error", attr: { role: "alert" } });
      setIcon(error.createSpan(), "circle-alert");
      error.createSpan({ text: this.errorMessage });
    }

    if (this.runningStage) {
      const live = section.createDiv({ cls: "aiw-live-output" });
      const liveHeader = live.createDiv({ cls: "aiw-live-header" });
      const pulse = liveHeader.createSpan({ cls: "aiw-pulse", attr: { "aria-hidden": "true" } });
      liveHeader.createSpan({ text: this.runningStage === "style" ? "正在分析个人偏好" : `正在生成${stageLabel(this.runningStage)}` });
      pulse.setAttr("title", "生成中");
      this.liveOutputEl = live.createEl("pre", { text: this.runningText || "等待模型响应..." });
      return;
    }

    this.liveOutputEl = null;
    const stage = this.activeStage;
    const value = getStageOutput(this.outputs, stage);
    this.renderPreviewTabs(section, stage);
    if (this.previewTab === "history") {
      this.renderHistory(section, stage);
      return;
    }
    if (this.previewTab === "diff" && stage === "review") {
      this.renderDiff(section);
      return;
    }
    if (this.previewTab === "images" && stage === "images") {
      this.renderImagePrompts(section);
      return;
    }

    if (!value) {
      const empty = section.createDiv({ cls: "aiw-manuscript-empty" });
      const guide = empty.createDiv({ cls: "aiw-empty-guide" });
      guide.createDiv({ cls: "aiw-guide-line" });
      guide.createDiv({ cls: "aiw-guide-line is-short" });
      guide.createDiv({ cls: "aiw-guide-line" });
      empty.createEl("h3", { text: `${stageLabel(stage)}尚未生成` });
      empty.createEl("p", { text: stage === "outline" ? "准备好素材后，从提纲开始。" : `完成上一阶段后即可生成${stageLabel(stage)}。` });
      return;
    }

    const artifactHeader = section.createDiv({ cls: "aiw-artifact-header" });
    const artifactTitle = artifactHeader.createDiv({ cls: "aiw-artifact-title" });
    artifactTitle.createEl("strong", { text: stageLabel(stage) });
    artifactTitle.createSpan({ text: `${formatCount(countReadableCharacters(value))} 字`, cls: "aiw-artifact-meta" });
    const artifactActions = artifactHeader.createDiv({ cls: "aiw-artifact-actions" });
    if (stage === "outline" && this.originalOutline && this.outputs.outline !== this.originalOutline) {
      this.createIconButton(artifactActions, "恢复初版提纲", "history", () => {
        this.outputs.outline = this.originalOutline;
        this.recordRevision("恢复初版提纲", "outline");
        void this.persistSession();
        this.render();
      });
    }
    this.createIconButton(artifactActions, `复制${stageLabel(stage)}`, "copy", () => void this.copyStageContent(stage, value));
    if (stage !== "outline") {
      this.createIconButton(artifactActions, "从工作区同步", "refresh-cw", async () => {
        const synced = await this.syncFromTargetFile({ force: true, stage: stage === "images" ? "images" : stage, announce: true });
        if (!synced) new Notice("工作区正文与插件稿件已经一致。");
      });
    }
    this.createIconButton(artifactActions, this.editingStage === stage ? "完成编辑" : `编辑${stageLabel(stage)}`, this.editingStage === stage ? "check" : "pencil", () => {
      const finishing = this.editingStage === stage;
      this.editingStage = finishing ? null : stage;
      if (finishing) {
        if (stage !== "images") this.clearDownstreamOutputs(stage);
        this.recordRevision(`手动编辑${stageLabel(stage)}`, stage);
        void this.persistSession();
      } else {
        this.recordRevision(`编辑${stageLabel(stage)}前备份`, stage);
        void this.persistSession();
      }
      this.render();
    });
    this.createIconButton(artifactActions, `重新生成${stageLabel(stage)}`, "rotate-ccw", () => void this.runStage(stage));

    if (this.editingStage === stage) {
      const editor = section.createEl("textarea", {
        cls: `aiw-artifact-editor ${stage === "outline" ? "is-outline" : ""}`.trim()
      });
      editor.value = value;
      editor.oninput = () => {
        if (stage === "images") this.outputs.illustrated = editor.value;
        else this.outputs[stage] = editor.value;
        this.schedulePersistSession();
      };
      editor.onchange = () => {
        void this.persistSession();
      };
    } else {
      this.renderMarkdown(section, value, "aiw-markdown-preview");
    }

    if (stage === "draft" || stage === "review" || stage === "images") {
      this.renderLengthStatus(section, value);
    }
    if (stage === "review" || stage === "images") {
      this.renderLint(section, value);
    }
    if (stage !== "images") this.renderNextStepInput(section, stage);
  }

  private renderPreviewTabs(root: HTMLElement, stage: WorkflowStage) {
    const tabs = root.createDiv({ cls: "aiw-tabs", attr: { role: "tablist" } });
    const definitions: Array<{ id: WriterPreviewTab; label: string; icon: string }> = [
      { id: "article", label: stage === "images" ? "带图成稿" : "内容", icon: "file-text" },
      ...(stage === "review" && this.outputs.draft && this.outputs.review
        ? [{ id: "diff" as const, label: "与初稿差异", icon: "diff" }]
        : []),
      ...(stage === "images" && this.imagePlaceholders.length > 0
        ? [{ id: "images" as const, label: `生图提示词 ${this.imagePlaceholders.length}`, icon: "images" }]
        : []),
      ...(this.getStageRevisions(stage).length > 0
        ? [{ id: "history" as const, label: "版本", icon: "history" }]
        : [])
    ];

    definitions.forEach((tab) => {
      const button = this.createButton(tabs, tab.label, tab.icon, () => {
        this.previewTab = tab.id;
        this.editingStage = null;
        this.schedulePersistSession();
        this.render();
      }, this.previewTab === tab.id ? "is-active" : "");
      button.setAttr("role", "tab");
      button.setAttr("aria-selected", String(this.previewTab === tab.id));
    });
  }

  private renderImagePrompts(root: HTMLElement) {
    if (this.imagePlaceholders.length === 0) {
      const empty = root.createDiv({ cls: "aiw-empty-inline" });
      setIcon(empty.createSpan(), "image-off");
      empty.createSpan({ text: "当前插图稿没有可用的生图提示词" });
      return;
    }
    const toolbar = root.createDiv({ cls: "aiw-image-prompt-toolbar" });
    const copy = toolbar.createDiv({ cls: "aiw-context-copy" });
    copy.createDiv({ text: `${this.imagePlaceholders.length} 张插图`, cls: "aiw-context-title" });
    copy.createDiv({ text: "逐张展开查看，或一次复制全部提示词", cls: "aiw-context-meta" });
    const actions = toolbar.createDiv({ cls: "aiw-image-prompt-actions" });
    const allExpanded = this.imagePlaceholders.every((item) => this.expandedImagePrompts.has(item.id));
    this.createButton(actions, allExpanded ? "全部收起" : "全部展开", allExpanded ? "chevrons-up" : "chevrons-down", () => {
      this.expandedImagePrompts = allExpanded
        ? new Set<string>()
        : new Set(this.imagePlaceholders.map((item) => item.id));
      this.render();
    }, "aiw-compact-button");
    this.createButton(actions, "复制全部", "copy", () => void this.copyAllImagePrompts(), "aiw-compact-button");

    const list = root.createDiv({ cls: "aiw-image-prompts" });
    this.imagePlaceholders.forEach((placeholder, index) => {
      const card = list.createEl("details", { cls: "aiw-image-prompt-card" });
      card.open = this.expandedImagePrompts.has(placeholder.id);
      card.ontoggle = () => {
        if (card.open) this.expandedImagePrompts.add(placeholder.id);
        else this.expandedImagePrompts.delete(placeholder.id);
      };
      const summary = card.createEl("summary", { cls: "aiw-image-prompt-summary" });
      const indexEl = summary.createSpan({ text: String(index + 1).padStart(2, "0"), cls: "aiw-image-prompt-index" });
      const summaryCopy = summary.createDiv({ cls: "aiw-context-copy" });
      const title = summaryCopy.createDiv({ cls: "aiw-image-prompt-title" });
      title.createEl("strong", { text: `插图 ${String(index + 1).padStart(2, "0")}` });
      title.createSpan({ text: placeholder.ratio, cls: "aiw-image-prompt-ratio" });
      summaryCopy.createDiv({ text: placeholder.description, cls: "aiw-image-prompt-description" });
      const body = card.createDiv({ cls: "aiw-image-prompt-body" });
      const bodyToolbar = body.createDiv({ cls: "aiw-image-prompt-header" });
      bodyToolbar.createEl("strong", { text: "中文生图提示词" });
      this.createButton(bodyToolbar, "复制此条", "copy", () => void this.copyImagePrompt(placeholder), "aiw-compact-button");
      const code = body.createEl("pre", { cls: "aiw-image-prompt-code" });
      code.createEl("code", { text: placeholder.prompt });
      body.createDiv({ text: `避免内容：${placeholder.avoid}`, cls: "aiw-image-prompt-meta" });
      body.createDiv({ text: `占位图：${placeholder.assetPath}`, cls: "aiw-image-prompt-path" });
    });
  }

  private async copyImagePrompt(placeholder: ImagePlaceholder) {
    const prompt = `${placeholder.prompt}\n\n建议比例：${placeholder.ratio}\n避免内容：${placeholder.avoid}`;
    try {
      await navigator.clipboard.writeText(prompt);
      new Notice("中文生图提示词已复制。");
    } catch {
      new Notice("复制失败，请手动复制代码块中的生图提示词。");
    }
  }

  private async copyAllImagePrompts() {
    const prompts = this.imagePlaceholders.map((placeholder, index) =>
      `插图 ${String(index + 1).padStart(2, "0")}\n${placeholder.prompt}\n建议比例：${placeholder.ratio}\n避免内容：${placeholder.avoid}`
    ).join("\n\n---\n\n");
    try {
      await navigator.clipboard.writeText(prompts);
      new Notice(`已复制 ${this.imagePlaceholders.length} 条中文生图提示词。`);
    } catch {
      new Notice("复制失败，请逐张展开后复制。");
    }
  }

  private async copyStageContent(stage: WorkflowStage, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      new Notice(`${stageLabel(stage)}已复制。`);
    } catch {
      new Notice("复制失败，请进入编辑模式后手动复制。");
    }
  }

  private renderHistory(root: HTMLElement, stage: WorkflowStage) {
    const history = root.createDiv({ cls: "aiw-history-list" });
    const revisions = this.getStageRevisions(stage);
    if (revisions.length === 0) {
      history.createDiv({ text: `还没有${stageLabel(stage)}的历史版本`, cls: "aiw-empty-inline" });
      return;
    }
    revisions.forEach((revision) => {
      const row = history.createEl("details", { cls: "aiw-history-row" });
      const summary = row.createEl("summary", { cls: "aiw-history-summary" });
      const icon = summary.createSpan({ cls: "aiw-history-icon", attr: { "aria-hidden": "true" } });
      setIcon(icon, "history");
      const copy = summary.createDiv({ cls: "aiw-context-copy" });
      copy.createDiv({ text: revision.reason, cls: "aiw-context-title" });
      copy.createDiv({
        text: new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(new Date(revision.createdAt)),
        cls: "aiw-context-meta"
      });
      const body = row.createDiv({ cls: "aiw-history-body" });
      body.createEl("pre", { text: getStageOutput(revision.snapshot.outputs, stage), cls: "aiw-history-preview" });
      const actions = body.createDiv({ cls: "aiw-history-actions" });
      this.createButton(actions, `恢复此${stageLabel(stage)}`, "history", async () => {
        const restored = restoreStageRevision(this.buildSession(), revision, stage);
        await this.restoreSession(restored);
        this.previewTab = "article";
        await this.persistSession();
        this.render();
        new Notice(`已恢复${stageLabel(stage)}：${revision.reason}，下游阶段已重置。`);
      }, "mod-warning");
    });
  }

  private getStageRevisions(stage: WorkflowStage) {
    const seen = new Set<string>();
    return [...this.history].reverse().filter((revision) => {
      const value = getStageOutput(revision.snapshot.outputs, stage);
      if (!value || seen.has(value)) return false;
      seen.add(value);
      return true;
    });
  }

  private renderDiff(root: HTMLElement) {
    const diff = root.createDiv({ cls: "aiw-diff", attr: { "aria-label": "初稿与审校稿差异" } });
    for (const part of createArticleDiff(this.outputs.draft, this.outputs.review)) {
      diff.createEl("pre", { text: part.value, cls: `is-${part.kind}` });
    }
  }

  private renderLint(root: HTMLElement, article: string) {
    const findings = [
      ...(this.articleStyle === "blog" ? lintKhazixArticle(article) : []),
      ...lintMarkdownStructure(article)
    ];
    const lint = root.createDiv({ cls: `aiw-lint ${findings.length === 0 ? "is-clean" : ""}` });
    const icon = lint.createSpan();
    setIcon(icon, findings.length === 0 ? "badge-check" : "list-checks");
    lint.createSpan({
      text: findings.length === 0 ? "本地硬规则检查通过" : `本地检查发现 ${findings.length} 项待核对`
    });
    if (findings.length > 0) {
      const list = lint.createEl("ul");
      findings.forEach((finding) => list.createEl("li", { text: `${finding.rule} · ${finding.excerpt}` }));
    }
  }

  private renderLengthStatus(root: HTMLElement, article: string) {
    const actual = countReadableCharacters(article);
    const withinTarget = isWithinTargetLength(actual, this.targetLength);
    const status = root.createDiv({ cls: `aiw-length-status ${withinTarget ? "is-within" : "is-outside"}` });
    const icon = status.createSpan();
    setIcon(icon, withinTarget ? "circle-check" : "ruler");
    status.createSpan({
      text: `约 ${formatCount(actual)} 字 · 目标 ${formatCount(this.targetLength)} 字（允许 ±10%）`
    });
  }

  private renderNextStepInput(root: HTMLElement, stage: Exclude<WorkflowStage, "images">) {
    const labels: Record<Exclude<WorkflowStage, "images">, string> = {
      outline: "生成初稿前，还要补充或修改什么",
      draft: "审校前，还要补充或修改什么",
      review: "插入占位图前，有什么额外要求"
    };
    const field = root.createDiv({ cls: "aiw-field aiw-feedback-field" });
    field.createEl("label", { text: labels[stage] });
    const input = field.createEl("textarea", {
      attr: { placeholder: "例如：第二部分加入我的实际经历；删掉第三点；语气克制一点；这段数据不要用。" }
    });
    input.value = this.feedback[stage];
    input.oninput = () => {
      this.feedback[stage] = input.value;
      this.schedulePersistSession();
    };
    input.onchange = () => void this.persistSession();
  }

  private renderFooter(root: HTMLElement) {
    const footer = root.createDiv({ cls: "aiw-footer" });

    if (this.runningStage) {
      const stop = this.createButton(footer, "停止", "square", () => this.cancel(), "mod-warning");
      stop.addClass("aiw-primary-action");
      return;
    }

    if (this.outputs.images || this.outputs.illustrated) {
      const writeBack = this.createButton(footer, "写回原文", "save", () => void this.writeBackArticle(), "mod-cta aiw-save-action");
      writeBack.addClass("aiw-primary-action");
      return;
    }

    const nextStage = this.getCurrentStage();
    const primary = this.createButton(footer, actionLabel(nextStage), actionIcon(nextStage), () => void this.runStage(nextStage), "mod-cta");
    primary.addClass("aiw-primary-action");
    primary.disabled = !this.targetPath
      || (this.contextItems.length === 0 && (this.articleStyle !== "custom" || !this.brief.trim()));

    if (this.outputs.review) {
      this.createButton(footer, "写回原文", "save", () => void this.writeBackArticle(), "aiw-save-action");
    }
  }

  private async runStage(stage: WorkflowStage) {
    if (!this.ensureReady()) return;
    const dependencies = requiredPreviousStage(stage);
    if (dependencies && !this.outputs[dependencies]) {
      new Notice(`请先完成${stageLabel(dependencies)}。`);
      return;
    }

    if (stage === "review") {
      await this.syncFromTargetFile({ force: false, stage: "draft", render: false });
    } else if (stage === "images") {
      await this.syncFromTargetFile({ force: false, stage: "review", render: false });
    }

    this.errorMessage = "";
    this.runningStage = stage;
    this.runningText = "";
    this.controller = new AbortController();
    this.render();

    try {
      const result = await runWorkflowStage({
        stage,
        brief: this.brief,
        articleStyle: this.articleStyle,
        stylePrompt: this.stylePrompt,
        context: assembleContext(this.contextItems),
        outputs: this.outputs,
        feedback: this.getFeedbackForStage(stage),
        targetLength: this.targetLength,
        articleType: this.plugin.settings.articleTypes[this.articleType],
        articleTemplate: await this.readCurrentArticleTemplate(),
        settings: this.plugin.settings,
        transport: createTransport(this.plugin.settings),
        signal: this.controller.signal,
        onUpdate: (value) => {
          this.runningText = value;
          this.liveOutputEl?.setText(value);
        },
        onRetry: (attempt) => {
          this.liveOutputEl?.setText(`连接中断，正在进行第 ${attempt} 次尝试...`);
        },
        onRepair: (markers) => {
          this.liveOutputEl?.setText(`发现未完成标记（${markers.join("、")}），正在自动修复...`);
        }
      });
      const cleaned = stripOuterFence(result);
      if (stage === "images") {
        const materialized = await materializeImagePlaceholders(this.app, this.targetPath, cleaned);
        this.outputs.images = cleaned;
        this.outputs.illustrated = materialized.markdown;
        this.imagePlaceholders = materialized.placeholders;
      } else {
        this.outputs[stage] = cleaned;
        this.clearDownstreamOutputs(stage);
      }
      if (stage === "outline" && !this.originalOutline) {
        this.originalOutline = this.outputs.outline;
      }
      if (stage === "outline") this.materialsCollapsed = true;
      this.activeStage = stage;
      this.editingStage = null;
      this.previewTab = defaultPreviewTab(stage, this.imagePlaceholders.length > 0);
      if (stage === "images" && this.imagePlaceholders[0]) {
        this.expandedImagePrompts = new Set([this.imagePlaceholders[0].id]);
      }
      this.recordRevision(`生成${stageLabel(stage)}`, stage);
      await this.persistSession();
    } catch (error) {
      if (!isAbortError(error)) {
        this.errorMessage = formatError(error);
      }
    } finally {
      this.runningStage = null;
      this.runningText = "";
      this.controller = null;
      this.render();
    }
  }

  private async readCurrentArticleTemplate() {
    const path = this.plugin.settings.articleTypes[this.articleType].templatePath;
    const file = this.app.vault.getAbstractFileByPath(path);
    return file instanceof TFile ? await this.app.vault.cachedRead(file) : "";
  }

  private async analyzePersonalStyle() {
    if (!this.ensureReady() || this.contextItems.length === 0) return;
    this.errorMessage = "";
    this.runningStage = "style";
    this.runningText = "";
    this.controller = new AbortController();
    this.render();

    try {
      const snapshot = assembleContext(this.contextItems, 60_000);
      const result = await createTransport(this.plugin.settings).generate({
        system: KHAZIX_SYSTEM_PROMPT,
        prompt: compilePersonalStylePrompt(snapshot),
        signal: this.controller.signal,
        onToken: (token) => {
          this.runningText += token;
          this.liveOutputEl?.setText(this.runningText);
        }
      });
      this.plugin.settings.personalPreferences = stripOuterFence(result).trim();
      await this.plugin.saveSettings();
      new Notice("个人偏好已分析并保存，可在设置中修改。\n");
    } catch (error) {
      if (!isAbortError(error)) this.errorMessage = formatError(error);
    } finally {
      this.runningStage = null;
      this.runningText = "";
      this.controller = null;
      this.render();
    }
  }

  private cancel() {
    this.controller?.abort();
  }

  private async writeBackArticle() {
    if (!this.targetPath) return;

    try {
      const target = this.app.vault.getAbstractFileByPath(this.targetPath);
      if (!(target instanceof TFile)) {
        throw new Error(`找不到写回目标：${this.targetPath}`);
      }
      await this.syncFromTargetFile({ force: false, render: false });
      const article = removeMeaninglessMarkdownMarkers(this.outputs.illustrated || this.outputs.review || this.outputs.draft);
      if (!article) return;

      const confirmed = window.confirm(`将只更新 ${target.path} 中由 AI Writer 托管的正文区域。frontmatter 属性和托管区域外内容保持不变；首次写回会建立正文区域标记。是否继续？`);
      if (!confirmed) return;

      const original = await this.readTargetMarkdown(target);
      const markdown = replaceManagedArticle(original, article);
      this.recordRevision("写回原文前");
      const view = this.getOpenTargetView();
      const replacement = createTextReplacement(original, markdown);
      this.pendingOwnWrite = { path: target.path, markdown };
      if (view && replacement) {
        view.editor.transaction({
          changes: [{
            from: view.editor.offsetToPos(replacement.from),
            to: view.editor.offsetToPos(replacement.to),
            text: replacement.text
          }]
        }, "ai-writer-write-back");
        await view.save();
      } else if (replacement) {
        await this.app.vault.modify(target, markdown);
      }
      await this.persistSession();
      if (view) await this.app.workspace.revealLeaf(view.leaf);
      else await this.app.workspace.getLeaf("tab").openFile(target);
      new Notice(replacement ? `成稿已更新托管正文区域：${target.path}` : "工作区正文已经是当前成稿。");
    } catch (error) {
      this.pendingOwnWrite = null;
      this.errorMessage = formatError(error);
      this.render();
    }
  }

  private ensureReady() {
    const settings = this.plugin.settings;
    if (!this.targetPath) {
      this.errorMessage = "请先点击“当前笔记”，指定最终写回的原文。";
      this.render();
      return false;
    }
    if (!settings.baseUrl.trim() || !settings.model.trim()) {
      this.errorMessage = "请先在 AI Writer 设置中填写 Base URL 和模型。";
      this.render();
      return false;
    }
    return true;
  }

  private getFeedbackForStage(stage: WorkflowStage) {
    if (stage === "draft") return this.feedback.outline;
    if (stage === "review") return this.feedback.draft;
    if (stage === "images") return this.feedback.review;
    return "";
  }

  private getCurrentStage(): WorkflowStage {
    if (!this.outputs.outline) return "outline";
    if (!this.outputs.draft) return "draft";
    if (!this.outputs.review) return "review";
    return "images";
  }

  private getWorkspaceSubtitle() {
    if (this.runningStage) return "生成期间可以随时停止";
    const value = getStageOutput(this.outputs, this.activeStage);
    return value ? `正在查看${stageLabel(this.activeStage)}，可编辑、复制或回溯版本` : `${stageLabel(this.activeStage)}等待生成`;
  }

  private createSection(root: HTMLElement, title: string, description: string) {
    const section = root.createDiv({ cls: "aiw-section" });
    const header = section.createDiv({ cls: "aiw-section-header" });
    const copy = header.createDiv();
    copy.createEl("h3", { text: title });
    copy.createEl("p", { text: description });
    return section;
  }

  private createButton(
    root: HTMLElement,
    label: string,
    icon: string,
    action: () => void | Promise<void>,
    extraClass = ""
  ) {
    const button = root.createEl("button", { cls: `aiw-button ${extraClass}`.trim() });
    const iconEl = button.createSpan({ cls: "aiw-button-icon", attr: { "aria-hidden": "true" } });
    setIcon(iconEl, icon);
    button.createSpan({ text: label });
    button.setAttr("title", label);
    button.onclick = () => void action();
    return button;
  }

  private createIconButton(root: HTMLElement, label: string, icon: string, action: () => void | Promise<void>) {
    const button = root.createEl("button", {
      cls: "aiw-icon-button",
      attr: { "aria-label": label, title: label }
    });
    setIcon(button, icon);
    button.onclick = () => void action();
    return button;
  }

  private renderMarkdown(root: HTMLElement, markdown: string, className: string) {
    const container = root.createDiv({ cls: className });
    void MarkdownRenderer.render(this.app, markdown, container, "", this);
  }
}

class MarkdownFileSuggest extends FuzzySuggestModal<TFile> {
  constructor(app: App, private readonly files: TFile[], private readonly choose: (file: TFile) => void) {
    super(app);
    this.setPlaceholder("搜索 Markdown 笔记...");
  }

  getItems() {
    return this.files;
  }

  getItemText(item: TFile) {
    return item.path;
  }

  onChooseItem(item: TFile) {
    this.choose(item);
  }
}

function requiredPreviousStage(stage: WorkflowStage): WorkflowStage | null {
  if (stage === "draft") return "outline";
  if (stage === "review") return "draft";
  if (stage === "images") return "review";
  return null;
}

function stageLabel(stage: WorkflowStage) {
  return STAGES.find((item) => item.id === stage)?.label || stage;
}

function actionLabel(stage: WorkflowStage) {
  if (stage === "outline") return "生成提纲";
  if (stage === "draft") return "生成初稿";
  if (stage === "review") return "审校成稿";
  return "插入占位图";
}

function actionIcon(stage: WorkflowStage) {
  if (stage === "outline") return "list-tree";
  if (stage === "draft") return "file-pen-line";
  if (stage === "review") return "scan-text";
  return "image-plus";
}

function defaultPreviewTab(stage: WorkflowStage, hasImagePrompts: boolean): WriterPreviewTab {
  return stage === "images" && hasImagePrompts ? "images" : "article";
}

function normalizePreviewTab(stage: WorkflowStage, tab: WriterPreviewTab, hasImagePrompts: boolean): WriterPreviewTab {
  if (tab === "diff" && stage !== "review") return defaultPreviewTab(stage, hasImagePrompts);
  if (tab === "images" && (stage !== "images" || !hasImagePrompts)) return "article";
  return tab;
}

function stripOuterFence(value: string) {
  return value.trim().replace(/^```(?:markdown|md)?\s*\n([\s\S]*?)\n```$/iu, "$1").trim();
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

function formatError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  if (/CORS|Failed to fetch|fetch failed/iu.test(message)) {
    return "流式连接被 CORS 阻止。请让 API 开启 CORS，或在设置中切换到非流式兼容模式。";
  }
  return message || "生成失败，请检查 API 设置后重试。";
}

function formatCount(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}
