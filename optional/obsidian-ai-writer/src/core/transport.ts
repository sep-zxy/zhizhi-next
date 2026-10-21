import OpenAI from "openai";
import { requestUrl } from "obsidian";
import type { AiTransport, AiWriterSettings, GenerationRequest } from "./types";

export function createTransport(settings: AiWriterSettings): AiTransport {
  return settings.transportMode === "compatible"
    ? new ObsidianRequestTransport(settings)
    : new OpenAiStreamingTransport(settings);
}

export function normalizeBaseUrl(value: string) {
  return value.trim().replace(/\/+$/u, "");
}

export function parseCustomHeaders(value: string): Record<string, string> {
  if (!value.trim()) {
    return {};
  }

  const parsed: unknown = JSON.parse(value);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("自定义 Header 必须是 JSON 对象。\n例如：{\"HTTP-Referer\":\"https://example.com\"}");
  }

  const headers: Record<string, string> = {};
  for (const [key, headerValue] of Object.entries(parsed)) {
    if (typeof headerValue !== "string") {
      throw new Error(`Header ${key} 的值必须是字符串。`);
    }
    headers[key] = headerValue;
  }

  return headers;
}

export function isRetryableError(error: unknown) {
  if (error instanceof OpenAI.APIError) {
    return error.status === 408 || error.status === 409 || error.status === 429 || (error.status ?? 0) >= 500;
  }

  if (error instanceof Error) {
    if (error.name === "AbortError") return false;
    return /network|fetch|timeout|ECONNRESET|ETIMEDOUT|HTTP (?:408|409|429|5\d\d)/iu.test(error.message);
  }

  return false;
}

class OpenAiStreamingTransport implements AiTransport {
  private readonly client: OpenAI;

  constructor(private readonly settings: AiWriterSettings) {
    this.client = new OpenAI({
      apiKey: settings.apiKey || "not-required",
      baseURL: normalizeBaseUrl(settings.baseUrl),
      dangerouslyAllowBrowser: true,
      defaultHeaders: parseCustomHeaders(settings.customHeaders),
      maxRetries: 0
    });
  }

  async generate(request: GenerationRequest) {
    const stream = await this.client.chat.completions.create({
      model: this.settings.model,
      messages: [
        { role: "system", content: request.system },
        { role: "user", content: request.prompt }
      ],
      temperature: this.settings.temperature,
      max_tokens: this.settings.maxTokens,
      stream: true
    }, { signal: request.signal });

    let result = "";
    for await (const chunk of stream) {
      if (request.signal.aborted) {
        throw createAbortError();
      }
      const token = chunk.choices[0]?.delta?.content || "";
      if (token) {
        result += token;
        request.onToken(token);
      }
    }

    return result;
  }

  async testConnection(signal: AbortSignal) {
    await this.client.chat.completions.create({
      model: this.settings.model,
      messages: [{ role: "user", content: "Reply with OK." }],
      max_tokens: 2,
      stream: false
    }, { signal });
  }
}

class ObsidianRequestTransport implements AiTransport {
  private readonly headers: Record<string, string>;

  constructor(private readonly settings: AiWriterSettings) {
    this.headers = {
      Authorization: `Bearer ${settings.apiKey || "not-required"}`,
      "Content-Type": "application/json",
      ...parseCustomHeaders(settings.customHeaders)
    };
  }

  async generate(request: GenerationRequest) {
    assertNotAborted(request.signal);
    const response = await this.post({
      model: this.settings.model,
      messages: [
        { role: "system", content: request.system },
        { role: "user", content: request.prompt }
      ],
      temperature: this.settings.temperature,
      max_tokens: this.settings.maxTokens,
      stream: false
    });
    assertNotAborted(request.signal);

    const content = extractResponseContent(response.json);
    request.onToken(content);
    return content;
  }

  async testConnection(signal: AbortSignal) {
    assertNotAborted(signal);
    await this.post({
      model: this.settings.model,
      messages: [{ role: "user", content: "Reply with OK." }],
      max_tokens: 2,
      stream: false
    });
    assertNotAborted(signal);
  }

  private async post(body: Record<string, unknown>) {
    const response = await requestUrl({
      url: `${normalizeBaseUrl(this.settings.baseUrl)}/chat/completions`,
      method: "POST",
      headers: this.headers,
      contentType: "application/json",
      body: JSON.stringify(body),
      throw: false
    });

    if (response.status < 200 || response.status >= 300) {
      throw new Error(`API 请求失败：HTTP ${response.status} ${safeResponseMessage(response.text)}`.trim());
    }

    return response;
  }
}

function extractResponseContent(value: unknown): string {
  if (!value || typeof value !== "object") {
    throw new Error("API 返回了无法识别的响应。需兼容 OpenAI Chat Completions 格式。\n");
  }

  const choices = (value as { choices?: unknown }).choices;
  if (!Array.isArray(choices)) {
    throw new Error("API 响应缺少 choices 数组。\n");
  }

  const first = choices[0] as { message?: { content?: unknown } } | undefined;
  if (typeof first?.message?.content !== "string") {
    throw new Error("API 响应缺少 choices[0].message.content。\n");
  }

  return first.message.content;
}

function safeResponseMessage(value: string) {
  return value.replace(/sk-[A-Za-z0-9_-]+/gu, "[API_KEY]").slice(0, 300);
}

function assertNotAborted(signal: AbortSignal) {
  if (signal.aborted) throw createAbortError();
}

function createAbortError() {
  return new DOMException("请求已取消。", "AbortError");
}
