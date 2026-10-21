import { App, Notice, PluginSettingTab, Setting } from "obsidian";
import type AiWriterPlugin from "./main";
import type { WorkflowStage } from "./core/types";
import { getDefaultStageTemplate } from "./core/prompts";
import type { ArticleTypeId } from "./core/types";

const STAGE_LABELS: Record<WorkflowStage, string> = {
  outline: "提纲 Prompt",
  draft: "初稿 Prompt",
  review: "审校 Prompt",
  images: "插入占位图 Prompt"
};

export class AiWriterSettingTab extends PluginSettingTab {
  constructor(app: App, private readonly plugin: AiWriterPlugin) {
    super(app, plugin);
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.addClass("aiw-settings");

    containerEl.createEl("h2", { text: "AI Writer" });
    containerEl.createEl("p", {
      cls: "setting-item-description",
      text: "API Key 保存在本地插件数据中。写作素材只发送到这里配置的 Base URL。"
    });

    new Setting(containerEl)
      .setName("Base URL")
      .setDesc("OpenAI Compatible API 根地址，通常以 /v1 结尾。")
      .addText((text) => text
        .setPlaceholder("https://api.openai.com/v1")
        .setValue(this.plugin.settings.baseUrl)
        .onChange(async (value) => {
          this.plugin.settings.baseUrl = value.trim();
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("API Key")
      .setDesc("本地明文保存，不会写入日志。")
      .addText((text) => {
        text.inputEl.type = "password";
        text
          .setPlaceholder("sk-...")
          .setValue(this.plugin.settings.apiKey)
          .onChange(async (value) => {
            this.plugin.settings.apiKey = value.trim();
            await this.plugin.saveSettings();
          });
      });

    new Setting(containerEl)
      .setName("模型")
      .setDesc("直接输入兼容服务提供的模型 ID。")
      .addText((text) => text
        .setPlaceholder("gpt-4.1-mini")
        .setValue(this.plugin.settings.model)
        .onChange(async (value) => {
          this.plugin.settings.model = value.trim();
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("请求模式")
      .setDesc("真实流式需要 API 支持 CORS；兼容模式绕过 CORS，但只能完整返回。")
      .addDropdown((dropdown) => dropdown
        .addOption("stream", "真实流式（fetch）")
        .addOption("compatible", "非流式兼容（requestUrl）")
        .setValue(this.plugin.settings.transportMode)
        .onChange(async (value) => {
          this.plugin.settings.transportMode = value === "compatible" ? "compatible" : "stream";
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("自定义 Header")
      .setDesc("可选 JSON 对象。Authorization 和 Content-Type 会自动添加。")
      .addTextArea((text) => text
        .setPlaceholder('{"HTTP-Referer":"https://example.com"}')
        .setValue(this.plugin.settings.customHeaders)
        .onChange(async (value) => {
          this.plugin.settings.customHeaders = value.trim();
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("测试连接")
      .setDesc("会发送一次最多 2 token 的 Chat Completions 请求。")
      .addButton((button) => button
        .setButtonText("测试")
        .onClick(async () => {
          button.setDisabled(true).setButtonText("连接中...");
          try {
            await this.plugin.testConnection();
            new Notice("AI Writer API 连接成功。\n");
          } catch (error) {
            new Notice(error instanceof Error ? error.message : "API 连接失败。\n");
          } finally {
            button.setDisabled(false).setButtonText("测试");
          }
        }));

    containerEl.createEl("h3", { text: "生成参数" });

    new Setting(containerEl)
      .setName("温度")
      .setDesc("控制表达变化，测试版建议保持 0.7。")
      .addSlider((slider) => slider
        .setLimits(0, 1.5, 0.1)
        .setDynamicTooltip()
        .setValue(this.plugin.settings.temperature)
        .onChange(async (value) => {
          this.plugin.settings.temperature = value;
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("最大输出 token")
      .setDesc("兼容服务对该参数的支持可能不同。")
      .addText((text) => text
        .setValue(String(this.plugin.settings.maxTokens))
        .onChange(async (value) => {
          const parsed = Number.parseInt(value, 10);
          if (Number.isFinite(parsed) && parsed >= 256) {
            this.plugin.settings.maxTokens = parsed;
            await this.plugin.saveSettings();
          }
        }));

    containerEl.createEl("h3", { text: "个人偏好" });
    new Setting(containerEl)
      .setName("风格补充")
      .setDesc("补充长期使用的表达偏好和禁忌；本篇文章结构请直接在写作面板修改文章提示词。")
      .addTextArea((text) => text
        .setPlaceholder("可在写作面板中从已选样本自动分析，也可以手动填写。")
        .setValue(this.plugin.settings.personalPreferences)
        .onChange(async (value) => {
          this.plugin.settings.personalPreferences = value.trim();
          await this.plugin.saveSettings();
        }));

    containerEl.createEl("h3", { text: "文章模板" });
    (["blog", "app-note", "software"] as ArticleTypeId[]).forEach((articleType) => {
      const config = this.plugin.settings.articleTypes[articleType];
      new Setting(containerEl)
        .setName(`${config.label}模板`)
        .setDesc("Vault 内的 Markdown 模板路径。")
        .addText((text) => text
          .setValue(config.templatePath)
          .onChange(async (value) => {
            config.templatePath = value.trim();
            await this.plugin.saveSettings();
          }));
    });

    const promptDetails = containerEl.createEl("details", { cls: "aiw-prompt-settings" });
    promptDetails.createEl("summary", { text: "阶段 Prompt（高级）" });
    promptDetails.createEl("p", {
      text: "留空使用内置阶段 Prompt。文章风格与结构请在写作面板直接修改；这里不能覆盖固定真实性规则。",
      cls: "setting-item-description"
    });

    (Object.keys(STAGE_LABELS) as WorkflowStage[]).forEach((stage) => {
      new Setting(promptDetails)
        .setName(STAGE_LABELS[stage])
        .setDesc(`可用变量请参考默认模板。当前默认：${getDefaultStageTemplate(stage).slice(0, 42)}...`)
        .addTextArea((text) => text
          .setValue(this.plugin.settings.promptOverrides[stage])
          .onChange(async (value) => {
            this.plugin.settings.promptOverrides[stage] = value;
            await this.plugin.saveSettings();
          }));
    });
  }
}
