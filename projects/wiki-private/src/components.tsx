import { Component, type ReactNode } from "react";
import { WikiApiError } from "./api";
import { text } from "./contracts";
import type { Resource } from "./state";

export function Icon({
  name = "book",
  size = 21,
}: {
  name?: string;
  size?: number;
}) {
  const paths: Record<string, ReactNode> = {
    book: (
      <>
        <path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Z" />
        <path d="M12 5v15" />
      </>
    ),
    home: (
      <>
        <path d="m3 10 9-7 9 7M5 9v12h5v-7h4v7h5V9" />
      </>
    ),
    inbox: (
      <>
        <path d="m4 4-2 11v5h20v-5L20 4Z" />
        <path d="M2 15h6l2 3h4l2-3h6" />
      </>
    ),
    star: <path d="m12 3 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1Z" />,
    task: (
      <>
        <rect x="4" y="3" width="16" height="18" rx="2" />
        <path d="m8 12 3 3 5-6" />
      </>
    ),
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2" />
      </>
    ),
    lock: (
      <>
        <rect x="5" y="10" width="14" height="11" rx="2" />
        <path d="M8 10V7a4 4 0 0 1 8 0v3" />
      </>
    ),
    search: (
      <>
        <circle cx="10" cy="10" r="7" />
        <path d="m15 15 6 6" />
      </>
    ),
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    link: (
      <>
        <path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 6v6l4 2" />
      </>
    ),
    file: (
      <>
        <path d="M5 2h9l5 5v15H5Z" />
        <path d="M14 2v6h5M8 12h8M8 16h8" />
      </>
    ),
    globe: (
      <>
        <circle cx="12" cy="12" r="9" />
        <ellipse cx="12" cy="12" rx="4" ry="9" />
        <path d="M3 12h18" />
      </>
    ),
    branch: (
      <>
        <path d="M6 4v12a4 4 0 0 0 8 0V8m-8 4 8-4" />
        <circle cx="6" cy="3" r="2" />
        <circle cx="14" cy="6" r="2" />
      </>
    ),
    alert: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 6v7m0 3v1" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] || paths.file}
    </svg>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={"badge badge--" + tone}>{children}</span>;
}
export function PageTitle({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}
export function Notice({
  title,
  children,
  tone = "info",
}: {
  title?: string;
  children: ReactNode;
  tone?: string;
}) {
  return (
    <div className={"notice notice--" + tone}>
      <Icon name="alert" />
      <div>
        {title && <strong>{title}</strong>}
        <div>{children}</div>
      </div>
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <section className="empty">
      <Icon name="book" size={32} />
      <h2>{title}</h2>
      {children && <p>{children}</p>}
    </section>
  );
}
export function Failure({
  error,
  retry,
}: {
  error: WikiApiError;
  retry?: () => void;
}) {
  return (
    <div role="alert" className="error-state">
      <Icon name="alert" size={27} />
      <h2>
        {error.status === 401
          ? "连接私有工作区"
          : error.status === 403
            ? "内容不可访问"
            : "暂时无法读取"}
      </h2>
      <p>{error.message}</p>
      <small>
        错误标识：{error.code}
        {error.traceId && error.traceId !== "UNKNOWN_ERROR"
          ? " · 追踪：" + error.traceId
          : ""}
      </small>
      {retry && (
        <button className="button" onClick={retry}>
          重新读取
        </button>
      )}
    </div>
  );
}
export function ResourceView<T>({
  resource,
  children,
}: {
  resource: Resource<T>;
  children: (data: T) => ReactNode;
}) {
  if (resource.status === "loading")
    return (
      <div role="status" className="loading">
        正在从私有服务读取…
      </div>
    );
  if (resource.status === "error")
    return <Failure error={resource.error!} retry={resource.reload} />;
  return <>{children(resource.data!)}</>;
}
export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
function inlineText(value: string): ReactNode[] {
  return value
    .split(/(`[^`]+`|\*\*[^*]+\*\*)/g)
    .map((part, index) =>
      part.startsWith("`") && part.endsWith("`") ? (
        <code key={index}>{part.slice(1, -1)}</code>
      ) : part.startsWith("**") && part.endsWith("**") ? (
        <strong key={index}>{part.slice(2, -2)}</strong>
      ) : (
        part
      ),
    );
}
/** Escaped text only: no HTML injection, remote images, executable links or embeds. */
export function MarkdownBody({ body }: { body: string }) {
  const blocks: ReactNode[] = [];
  let code: string[] | null = null;
  let paragraph: string[] = [];
  let list: { ordered: boolean; start: number; items: string[] } | null = null;
  function flushList() {
    if (!list) return;
    const children = list.items.map((item, index) => (
      <li key={index}>{inlineText(item)}</li>
    ));
    blocks.push(
      list.ordered ? (
        <ol key={"l" + blocks.length} start={list.start}>
          {children}
        </ol>
      ) : (
        <ul key={"l" + blocks.length}>{children}</ul>
      ),
    );
    list = null;
  }
  function flush() {
    if (paragraph.length) {
      blocks.push(
        <p key={"p" + blocks.length}>{inlineText(paragraph.join("\n"))}</p>,
      );
      paragraph = [];
    }
  }
  for (const line of body.split("\n")) {
    if (/^```/.test(line)) {
      flushList();
      flush();
      if (code) {
        blocks.push(
          <pre key={"c" + blocks.length}>
            <code>{code.join("\n")}</code>
          </pre>,
        );
        code = null;
      } else code = [];
      continue;
    }
    if (code) {
      code.push(line);
      continue;
    }
    const bullet = /^\s*(?:([-+*])|([0-9]+)\.)\s+(.+)$/.exec(line);
    if (bullet) {
      flush();
      const ordered = !!bullet[2];
      if (list && list.ordered !== ordered) flushList();
      if (!list)
        list = { ordered, start: ordered ? Number(bullet[2]) : 1, items: [] };
      list.items.push(bullet[3]);
      continue;
    }
    flushList();
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      flush();
      blocks.push(
        heading[1].length === 1 ? (
          <h2 key={"h" + blocks.length}>{inlineText(heading[2])}</h2>
        ) : (
          <h3 key={"h" + blocks.length}>{inlineText(heading[2])}</h3>
        ),
      );
    } else if (!line.trim()) flush();
    else paragraph.push(line);
  }
  flush();
  flushList();
  if (code)
    blocks.push(
      <pre key="unclosed-code">
        <code>{code.join("\n")}</code>
      </pre>,
    );
  return (
    <div className="markdown-body">
      {blocks.length ? blocks : <p className="muted">正文为空。</p>}
    </div>
  );
}
export class Boundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <Failure
        error={new WikiApiError("UI_ERROR", 0)}
        retry={() => window.location.reload()}
      />
    ) : (
      this.props.children
    );
  }
}
export function SafeExternal({
  url,
  children,
}: {
  url: unknown;
  children: ReactNode;
}) {
  const value = text(url);
  try {
    const parsed = new URL(value);
    if (
      !["https:", "http:"].includes(parsed.protocol) ||
      parsed.username ||
      parsed.password
    )
      return <span>{children}</span>;
  } catch {
    return <span>{children}</span>;
  }
  return (
    <a href={value} rel="noopener noreferrer" target="_blank">
      {children}
    </a>
  );
}
