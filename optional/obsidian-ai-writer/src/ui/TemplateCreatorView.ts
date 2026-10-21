import {
  ButtonComponent,
  DropdownComponent,
  ItemView,
  Notice,
  Setting,
  TextComponent,
  setIcon,
  type WorkspaceLeaf
} from "obsidian";
import type AiWriterPlugin from "../main";
import type { ArticleTypeId } from "../core/types";
import { createNamedArticleFromTemplate } from "../core/templates";

export const TEMPLATE_CREATOR_VIEW_TYPE = "ai-writer-template-creator";

export class TemplateCreatorView extends ItemView {
  private targetFolder = "";
  private articleType: ArticleTypeId = "blog";
  private articleName = "";
  private creating = false;

  constructor(leaf: WorkspaceLeaf, private readonly plugin: AiWriterPlugin) {
    super(leaf);
  }

  getViewType() {
    return TEMPLATE_CREATOR_VIEW_TYPE;
  }

  getDisplayText() {
    return "按模板新建";
  }

  getIcon() {
    return "file-plus-2";
  }

  async onOpen() {
    this.render();
  }

  async onClose() {
    this.contentEl.empty();
  }

  setTargetFolder(folder: string) {
    this.targetFolder = folder.replace(/\\/gu, "/").replace(/^\/+|\/+$/gu, "");
    this.render();
  }

  private render() {
    const root = this.contentEl;
    root.empty();
    root.addClass("aiw-template-view");

    const page = root.createDiv({ cls: "aiw-template-page" });
    const header = page.createDiv({ cls: "aiw-page-header" });
    const mark = header.createDiv({ cls: "aiw-page-icon" });
    setIcon(mark, "file-plus-2");
    const title = header.createDiv();
    title.createEl("h2", { text: "按模板新建" });
    title.createEl("p", { text: "在当前笔记所在文件夹创建新文件" });

    const panel = page.createDiv({ cls: "aiw-native-panel" });
    new Setting(panel)
      .setName("创建位置")
      .setDesc(this.targetFolder || "Vault 根目录")
      .addExtraButton((button) => button
        .setIcon("locate-fixed")
        .setTooltip("重新读取当前活动笔记的文件夹")
        .onClick(() => this.setTargetFolder(this.plugin.getActiveFolderPath())));

    let typeDropdown: DropdownComponent;
    new Setting(panel)
      .setName("模板")
      .setDesc(this.templateDescription())
      .addDropdown((dropdown) => {
        typeDropdown = dropdown;
        (["blog", "app-note", "software"] as ArticleTypeId[]).forEach((id) => {
          dropdown.addOption(id, this.plugin.settings.articleTypes[id].label);
        });
        dropdown.setValue(this.articleType).onChange((value) => {
          this.articleType = value as ArticleTypeId;
          this.render();
        });
      });
    void typeDropdown!;

    let nameInput: TextComponent;
    new Setting(panel)
      .setName("文章名称")
      .setDesc("同时作为一级标题和文件名；重名会自动追加 -2、-3。")
      .addText((text) => {
        nameInput = text;
        text
          .setPlaceholder("例如：用 Codex 沉淀工程经验")
          .setValue(this.articleName)
          .onChange((value) => {
            this.articleName = value;
          });
        text.inputEl.addEventListener("keydown", (event) => {
          if (event.key === "Enter" && !event.isComposing) {
            event.preventDefault();
            void this.createArticle();
          }
        });
      });
    void nameInput!;

    const actions = page.createDiv({ cls: "aiw-page-actions" });
    new ButtonComponent(actions)
      .setButtonText(this.creating ? "创建中…" : "创建并打开")
      .setCta()
      .setDisabled(this.creating || !this.articleName.trim())
      .onClick(() => void this.createArticle());

    const hint = page.createDiv({ cls: "aiw-page-hint" });
    setIcon(hint.createSpan(), "info");
    hint.createSpan({ text: `使用 ${this.plugin.settings.articleTypes[this.articleType].templatePath}` });
  }

  private templateDescription() {
    const config = this.plugin.settings.articleTypes[this.articleType];
    return `${config.label} · ${config.templatePath}`;
  }

  private async createArticle() {
    if (this.creating || !this.articleName.trim()) return;
    this.creating = true;
    this.render();
    try {
      const file = await createNamedArticleFromTemplate(
        this.app,
        this.plugin.settings,
        this.articleType,
        this.articleName,
        this.targetFolder
      );
      this.articleName = "";
      await this.app.workspace.getLeaf("tab").openFile(file);
      new Notice(`已创建：${file.path}`);
    } catch (error) {
      new Notice(error instanceof Error ? error.message : "创建文章失败。");
    } finally {
      this.creating = false;
      this.render();
    }
  }
}
