import Mustache from "mustache";
import type { AiWriterSettings, ArticleStyleId, ArticleTypeConfig, ContextSnapshot, WorkflowOutputs, WorkflowStage } from "./types";
import { ARTICLE_ARCHETYPES, KHAZIX_AUDIT_RULES, KHAZIX_SYSTEM_PROMPT } from "../style/khazixStylePack";
import { getArticleStyle } from "./articleStyles";

const ARCHETYPE_TEXT = ARTICLE_ARCHETYPES.map((item) => `- ${item.name}：${item.focus}`).join("\n");

const CORE_WRITER_SYSTEM_PROMPT = `
你是用户的中文写作协作者。

规则优先级从高到低如下：
1. 素材真实性：只把素材明确支持的内容写成事实，不编造作者经历、人物细节、数据、引语、测试结果或情绪反应。
2. 公共知识：可以补充稳定的背景、通用原理和可复现方法，但不能把模型常识伪装成用户亲历；时效性事实没有素材支持时不得确定性陈述。
3. 完成度：缺少个人事实时删除或改写相关叙述，不得留下「待补充」、TODO、TBD、XXX、placeholder 或空 Markdown 结构。
4. 写作任务：文章风格决定表达与结构，模板类型决定 frontmatter 和章节兼容性，素材决定事实边界。
5. Markdown：根据内容合理使用标题、列表、表格、引用、代码块和 Obsidian 语法，禁止为了排版而堆叠结构。

只输出当前阶段要求的结果，不解释系统规则。
`.trim();

const GENERAL_AUDIT_RULES = `
按四层检查当前文章：

L1 真实性：核对事实、经历、数据、引语和时效性信息是否有素材支持。
L2 结构：检查开头是否直接、主线是否清楚、章节是否匹配文章风格与模板。
L3 可读性：检查列表、表格、引用和代码块是否真正提升理解，删掉机械重复和空泛套话。
L4 完成度：清除占位符、空标题、空列表、AI 式总结和没有信息量的段落。

修改时保留素材中的事实、链接和必要术语，不得为了润色捏造新细节。
`.trim();

const DEFAULT_STAGE_TEMPLATES: Record<WorkflowStage, string> = {
  outline: `
阅读素材并完成写作策划。

用户的写作目标
{{{brief}}}

文章类型与结构职责
{{{articleTypeLabel}}}：{{{articleTypeGuidance}}}

目标笔记模板
{{{articleTemplate}}}

目标成稿字数
约 {{{targetLength}}} 字，后续初稿和审校稿应控制在目标值的正负 10% 内。

可选文章原型
{{{archetypes}}}

请输出：
1. 一句话核心判断。
2. HKR 选题检查，分别说明 Happy、Knowledge、Resonance 是否成立。
3. 最适合的文章原型和理由。
4. 素材中可确认的事实、用户观点、真实经历。
5. 说明哪些内容可用公共知识补充，哪些个人事实因素材不足应从正文省略；不要生成任何占位符。
6. 一份可直接修改的文章提纲，标出开头钩子、主线推进、回环点和结尾落点。

素材快照
{{{context}}}
`,
  draft: `
根据已经确认的提纲写出完整中文长文初稿。

写作目标
{{{brief}}}

文章类型与结构职责
{{{articleTypeLabel}}}：{{{articleTypeGuidance}}}

目标笔记模板
{{{articleTemplate}}}

确认后的提纲
{{{outline}}}

用户在提纲之后补充的修改要求
{{{feedback}}}

目标成稿字数
约 {{{targetLength}}} 字，有效正文字符应控制在 {{{minimumLength}}} 至 {{{maximumLength}}} 之间。

个人补充偏好
{{{preferences}}}

素材快照
{{{context}}}

只输出可直接写入 Obsidian 的完整 Markdown 正文，不要使用 Markdown 代码围栏包住全文。
- 必须有且只有一个一级标题作为文章标题。
- 根据文章结构使用有意义的二级、三级标题，不能把全文写成没有层次的纯文字。
- 引用、列表、表格、代码块、Obsidian callout 和 [[双链]] 只在内容确实需要且素材可支持时使用。
- 步骤和并列事项优先使用列表；存在三个以上同维度比较项时优先使用表格；真正的核心判断可用 1 至 3 处「>」引用突出。
- 不要输出写作说明、审校报告或插图计划。
- 可以补充稳定的公共知识和通用解释。素材没有提供的第一手经历、私有数据或原话不得编造，应删除或改写为不依赖个人事实的表达。
- 正文不得出现「待补充」、TODO、TBD、XXX、placeholder 或任何类似未完成标记。
- 不得出现只有 #、##、>、-、* 等符号的空标题、空引用或空列表项。
`,
  review: `
审校并重写下面的初稿。

{{{auditRules}}}

写作目标
{{{brief}}}

文章类型与结构职责
{{{articleTypeLabel}}}：{{{articleTypeGuidance}}}

目标笔记模板
{{{articleTemplate}}}

确认后的提纲
{{{outline}}}

用户在初稿之后补充的修改要求
{{{feedback}}}

目标成稿字数
约 {{{targetLength}}} 字，有效正文字符应控制在 {{{minimumLength}}} 至 {{{maximumLength}}} 之间。必须主动删减或扩展到该范围。

素材快照
{{{context}}}

初稿
{{{draft}}}

只输出修改后的完整 Obsidian Markdown 正文，不要使用代码围栏包住全文，不要输出审校报告或插图计划。保留一级标题和必要的二级、三级结构，保留有来源支持的事实，不新增未经素材支持的经历、数据和引语。主动使用适合文章类型的列表、表格、引用或代码块，并清除所有未完成标记与空 Markdown 结构。
`,
  images: `
把下面的成稿改造成带插图占位标记的完整文章。不要生成真实图片，也不要把插图集中列在文章末尾。

用户对插图的补充要求
{{{feedback}}}

请直接在最合适的段落之间插入下面格式的标记，每个字段只能占一行：
<!-- aiw-image:start
description: 给读者看的简洁中文插图说明
ratio: 16:9
prompt: 可直接用于生图的完整单行 Prompt
avoid: 需要避免的内容，使用单行文本
aiw-image:end -->

要求：
- 保留完整文章正文和原有 Markdown 结构，只增加必要的插图标记。
- 只在真正需要视觉解释、步骤展示、对比或节奏转换的位置安排图片，通常 1 至 4 张。
- 标记必须位于具体段落之间，不能放在一级标题之前，不能集中堆到文章末尾。
- description、prompt、avoid 必须全部使用简体中文并保持单行。产品名、软件名等英文专有名词可以保留，但不得输出整句或整段英文提示词。
- prompt 要用自然、具体的中文描述主体、环境、构图、视角、光线、色彩和视觉风格，可以直接复制给中文生图模型使用。
- 不要使用冒号以外的自定义字段。
- 只输出带标记的完整 Markdown 文章，不要输出额外的插图规划报告。

成稿
{{{article}}}
`
};

export interface PromptInput {
  stage: WorkflowStage;
  brief: string;
  articleStyle?: ArticleStyleId;
  stylePrompt?: string;
  context: ContextSnapshot;
  outputs: WorkflowOutputs;
  settings: AiWriterSettings;
  feedback: string;
  targetLength: number;
  articleType?: ArticleTypeConfig;
  articleTemplate?: string;
}

export function compileStagePrompt(input: PromptInput) {
  const customTemplate = input.settings.promptOverrides[input.stage].trim();
  const template = customTemplate || DEFAULT_STAGE_TEMPLATES[input.stage];
  const article = input.outputs.review || input.outputs.draft;
  const minimumLength = Math.round(input.targetLength * 0.9);
  const maximumLength = Math.round(input.targetLength * 1.1);
  const articleType = input.articleType || input.settings.articleTypes.blog;
  const articleStyle = input.articleStyle || articleType.id;
  const style = getArticleStyle(articleStyle);
  const stylePrompt = input.stylePrompt?.trim() || style.prompt;
  const baseSystem = articleStyle === "blog" ? KHAZIX_SYSTEM_PROMPT : CORE_WRITER_SYSTEM_PROMPT;
  const auditRules = articleStyle === "blog" ? KHAZIX_AUDIT_RULES : GENERAL_AUDIT_RULES;
  const stageRules = input.stage === "images"
    ? "\n插图硬规则：所有 description、prompt、avoid 字段必须使用简体中文。英文产品名或专有名词可以保留，但字段主体不得是英文；该规则不能被自定义提示词覆盖。"
    : "";

  return {
    system: [
      baseSystem,
      `\n当前文章风格为「${style.label}」。以下风格提示词由用户选择并可直接修改，必须参与提纲、初稿和审校：\n${stylePrompt}`,
      `\n当前文章类型为「${articleType.label}」。结构职责：${articleType.guidance}\n目标模板如下，生成结构必须与它兼容：\n${input.articleTemplate?.trim() || "未读取到模板正文，按文章类型规则组织。"}`,
      stageRules,
      input.settings.personalPreferences.trim()
        ? `\n用户的个人补充偏好如下。它不能覆盖前面的真实性和主风格规则。\n${input.settings.personalPreferences.trim()}`
        : ""
    ].join("\n"),
    prompt: Mustache.render(template, {
      brief: input.brief.trim() || "把已选择的素材扩展成一篇完整文章。",
      archetypes: ARCHETYPE_TEXT,
      context: input.context.text,
      outline: input.outputs.outline,
      draft: input.outputs.draft,
      article,
      preferences: input.settings.personalPreferences.trim() || "无额外个人偏好。",
      auditRules,
      feedback: input.feedback.trim() || "无额外补充，按当前提纲和素材继续。",
      targetLength: input.targetLength,
      minimumLength,
      maximumLength,
      articleTypeLabel: articleType.label,
      articleTypeGuidance: articleType.guidance,
      articleStyleLabel: style.label,
      stylePrompt,
      articleTemplate: input.articleTemplate?.trim() || "未读取到模板正文，按文章类型规则组织。"
    })
  };
}

export function getDefaultStageTemplate(stage: WorkflowStage) {
  return DEFAULT_STAGE_TEMPLATES[stage].trim();
}

export const PERSONAL_STYLE_ANALYSIS_PROMPT = `
分析下面的用户写作样本，只提炼能够补充 Khazix 主风格的个人偏好。

输出简洁的 Markdown，包含：常用表达、目标读者、语气强度、偏好的例子类型、明确禁忌。不要复制样本中的整段文字，不总结样本主题，不推断敏感个人信息。

样本
{{{context}}}
`.trim();

export function compilePersonalStylePrompt(context: ContextSnapshot) {
  return Mustache.render(PERSONAL_STYLE_ANALYSIS_PROMPT, { context: context.text });
}
