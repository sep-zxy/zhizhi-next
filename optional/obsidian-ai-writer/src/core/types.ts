export type TransportMode = "stream" | "compatible";

export type WorkflowStage = "outline" | "draft" | "review" | "images";

export type ArticleTypeId = "blog" | "app-note" | "software";

export type ArticleStyleId = ArticleTypeId | "custom";

export interface ArticleStyleConfig {
  id: ArticleStyleId;
  label: string;
  description: string;
  prompt: string;
}

export interface ArticleTypeConfig {
  id: ArticleTypeId;
  label: string;
  templatePath: string;
  outputFolder: string;
  guidance: string;
}

export type ContextKind = "selection" | "note" | "brief";

export interface PromptOverrides {
  outline: string;
  draft: string;
  review: string;
  images: string;
}

export interface AiWriterSettings {
  baseUrl: string;
  apiKey: string;
  model: string;
  transportMode: TransportMode;
  temperature: number;
  maxTokens: number;
  customHeaders: string;
  personalPreferences: string;
  promptOverrides: PromptOverrides;
  articleTypes: Record<ArticleTypeId, ArticleTypeConfig>;
}

export interface ContextItem {
  id: string;
  kind: ContextKind;
  label: string;
  path?: string;
  content: string;
  charCount: number;
}

export interface ContextSnapshot {
  items: ContextItem[];
  text: string;
  charCount: number;
  truncated: boolean;
}

export interface PersistedContextRef {
  id: string;
  kind: ContextKind;
  label: string;
  path?: string;
  content?: string;
}

export interface ImagePlaceholder {
  id: string;
  description: string;
  prompt: string;
  ratio: string;
  avoid: string;
  assetPath: string;
}

export interface WorkflowOutputs {
  outline: string;
  draft: string;
  review: string;
  images: string;
  illustrated: string;
}

export type StageFeedback = Record<WorkflowStage, string>;

export type WriterPreviewTab = "article" | "diff" | "images" | "history";

export interface WriterSessionSnapshot {
  articleType: ArticleTypeId;
  articleStyle: ArticleStyleId;
  stylePrompt: string;
  brief: string;
  targetLength: number;
  contextRefs: PersistedContextRef[];
  originalOutline: string;
  outputs: WorkflowOutputs;
  imagePlaceholders: ImagePlaceholder[];
  feedback: StageFeedback;
  activeStage: WorkflowStage;
  previewTab: WriterPreviewTab;
}

export interface WriterRevision {
  id: string;
  reason: string;
  stage?: WorkflowStage;
  createdAt: string;
  snapshot: WriterSessionSnapshot;
}

export interface WriterSession extends WriterSessionSnapshot {
  targetPath: string;
  history: WriterRevision[];
  updatedAt: string;
}

export interface AiWriterPluginData {
  schemaVersion: 4;
  settings: AiWriterSettings;
  sessions: Record<string, WriterSession>;
}

export interface GenerationRequest {
  system: string;
  prompt: string;
  onToken: (token: string) => void;
  signal: AbortSignal;
}

export interface AiTransport {
  generate(request: GenerationRequest): Promise<string>;
  testConnection(signal: AbortSignal): Promise<void>;
}

export const DEFAULT_SETTINGS: AiWriterSettings = {
  baseUrl: "https://api.openai.com/v1",
  apiKey: "",
  model: "gpt-4.1-mini",
  transportMode: "stream",
  temperature: 0.7,
  maxTokens: 8192,
  customHeaders: "",
  personalPreferences: "",
  promptOverrides: {
    outline: "",
    draft: "",
    review: "",
    images: ""
  },
  articleTypes: {
    blog: {
      id: "blog",
      label: "博客",
      templatePath: "templates/blog-note-template.md",
      outputFolder: "notes/blog",
      guidance: "以叙述和观点推进为主；比较、流程或清单场景使用表格与列表；用少量引用突出核心判断。"
    },
    "app-note": {
      id: "app-note",
      label: "应用笔记",
      templatePath: "templates/app-note-template.md",
      outputFolder: "notes/apps",
      guidance: "围绕适用场景、配置或操作步骤、真实使用体验和相关链接组织；步骤使用有序列表，注意事项使用引用。"
    },
    software: {
      id: "software",
      label: "软件笔记",
      templatePath: "templates/software-note-template.md",
      outputFolder: "notes/software",
      guidance: "围绕软件定位、适用与不适用场景、功能判断、限制和来源组织；对比信息优先使用表格。"
    }
  }
};

export const EMPTY_OUTPUTS: WorkflowOutputs = {
  outline: "",
  draft: "",
  review: "",
  images: "",
  illustrated: ""
};

export const EMPTY_FEEDBACK: StageFeedback = {
  outline: "",
  draft: "",
  review: "",
  images: ""
};

export function createEmptySession(targetPath: string, articleType: ArticleTypeId = "blog"): WriterSession {
  return {
    targetPath,
    articleType,
    articleStyle: articleType,
    stylePrompt: "",
    brief: "",
    targetLength: 4000,
    contextRefs: [],
    originalOutline: "",
    outputs: structuredClone(EMPTY_OUTPUTS),
    imagePlaceholders: [],
    feedback: structuredClone(EMPTY_FEEDBACK),
    activeStage: "outline",
    previewTab: "article",
    history: [],
    updatedAt: new Date().toISOString()
  };
}
