import {
  useEffect,
  useState,
  useSyncExternalStore,
  type FormEvent,
} from "react";
import { defaultApi, WikiApiError, type WikiApi } from "./api";
import { Boundary, Failure, Icon } from "./components";
import { CatalogPage, EntryPage, HomePage } from "./knowledge-pages";
import {
  InboxPage,
  IntakePage,
  ReviewPage,
  ReviewsPage,
  SettingsPage,
  TasksPage,
  ToolsPage,
} from "./workflow-pages";
import { navigate, routeHref, safeNavigation, useRoute } from "./state";
import { BrowserAccess } from "./local-test-access";

declare global {
  interface Window {
    __WIKI_PRIVATE_CONFIG__?: {
      engineeringUrl?: string;
      publicWikiUrl?: string;
    };
  }
}
const nav = [
  ["home", "首页", "home"],
  ["inbox", "收件箱", "inbox"],
  ["catalog", "知识目录", "book"],
  ["reviews", "重要确认", "star"],
  ["tasks", "任务记录", "task"],
  ["tools", "工具与 Skill", "grid"],
  ["settings", "设置", "settings"],
];
export function WikiApp({ api = defaultApi }: { api?: WikiApi }) {
  const blocked = useSyncExternalStore(
    api.subscribeAccess,
    api.isAccessBlocked,
    () => false,
  );
  const localSession = useSyncExternalStore(
    api.subscribeBrowserSession,
    api.getBrowserSession,
    api.getBrowserSession,
  );
  const route = useRoute();
  const canRetry = useSyncExternalStore(
    api.subscribeBrowserSession,
    api.canRetryAccess,
    () => false,
  );
  const [search, setSearch] = useState("");
  useEffect(() => {
    if (blocked) setSearch("");
  }, [blocked]);
  const [menu, setMenu] = useState(false);
  const trustedLearning = useSyncExternalStore(
    api.subscribeBrowserSession,
    api.getLearningViewerUrl,
    api.getLearningViewerUrl,
  );
  const engineering =
    trustedLearning === undefined
      ? safeNavigation(window.__WIKI_PRIVATE_CONFIG__?.engineeringUrl, "")
      : trustedLearning || "";
  const publicUrl = safeNavigation(
    window.__WIKI_PRIVATE_CONFIG__?.publicWikiUrl,
    "",
  );
  const active =
    (
      { entry: "catalog", intake: "inbox", review: "reviews" } as Record<
        string,
        string
      >
    )[route.page] || route.page;
  function find(event: FormEvent) {
    event.preventDefault();
    const q = search.trim();
    navigate(
      routeHref("catalog", "", q ? new URLSearchParams({ q }) : undefined),
    );
    setMenu(false);
  }
  let page;
  switch (route.page) {
    case "home":
      page = <HomePage api={api} engineeringUrl={engineering} />;
      break;
    case "catalog":
      page = <CatalogPage api={api} query={route.query} />;
      break;
    case "entry":
      page = (
        <EntryPage
          api={api}
          id={route.id}
          query={route.query}
          engineeringUrl={engineering}
        />
      );
      break;
    case "inbox":
      page = <InboxPage api={api} />;
      break;
    case "intake":
      page = <IntakePage api={api} id={route.id} />;
      break;
    case "reviews":
      page = <ReviewsPage api={api} />;
      break;
    case "review":
      page = <ReviewPage api={api} id={route.id} />;
      break;
    case "tasks":
      page = <TasksPage api={api} id={route.id} />;
      break;
    case "settings":
      page = <SettingsPage api={api} />;
      break;
    case "tools":
      page = <ToolsPage />;
      break;
    default:
      page = (
        <section className="empty">
          <h1>页面不存在</h1>
          <a className="button" href="#/home">
            回到私有首页
          </a>
        </section>
      );
  }
  return (
    <Boundary>
      <div className="wiki-shell">
        <a
          className="skip-link"
          href="#main-content"
          onClick={(e) => {
            e.preventDefault();
            document.getElementById("main-content")?.focus();
          }}
        >
          跳至主要内容
        </a>
        <header className="topbar">
          <a className="brand" href="#/home">
            <Icon name="book" size={31} />
            <strong>个人 Wiki</strong>
          </a>
          <span className="private-label">
            <Icon name="lock" size={12} />
            {localSession.status === "authenticated" &&
            localSession.fixture === true
              ? "本机隔离测试"
              : "私有工作区"}
          </span>
          {!blocked && (
            <form role="search" className="global-search" onSubmit={find}>
              <Icon name="search" />
              <input
                aria-label="搜索私有知识"
                placeholder="搜索知识标题、正文与标签…"
                value={search}
                maxLength={500}
                onChange={(e) => setSearch(e.target.value)}
              />
              <button type="submit" aria-label="搜索">
                搜索
              </button>
            </form>
          )}
          {publicUrl ? (
            <a className="public-link" href={publicUrl} rel="noreferrer">
              <Icon name="globe" size={17} />
              公开阅读站 ↗
            </a>
          ) : (
            <span
              className="public-link unavailable"
              title="公开站尚未提供经过验证的同源入口"
            >
              <Icon name="globe" size={17} />
              公开站 · 待连接
            </span>
          )}
          <button
            className="menu-toggle button"
            onClick={() => setMenu(!menu)}
            aria-expanded={menu}
            aria-controls="wiki-navigation"
          >
            菜单
          </button>
        </header>
        <aside
          className={"sidebar" + (menu ? " is-open" : "")}
          id="wiki-navigation"
        >
          <nav aria-label="私有工作区导航">
            {nav.map(([key, label, icon]) => (
              <a
                key={key}
                href={routeHref(key)}
                aria-current={active === key ? "page" : undefined}
                onClick={() => setMenu(false)}
              >
                <Icon name={icon} />
                <span>{label}</span>
                {key === "tools" && <small>规划</small>}
              </a>
            ))}
          </nav>
          <div className="cross-domain">
            {engineering ? (
              <a href={engineering} rel="noreferrer">
                <Icon name="branch" />
                知枝 · 工程学习 <span>↗</span>
              </a>
            ) : (
              <span className="unavailable-link">
                <Icon name="branch" />
                知枝 · 入口待连接
              </span>
            )}
            {publicUrl ? (
              <a href={publicUrl} rel="noreferrer">
                <Icon name="globe" />
                公开 Wiki <span>↗</span>
              </a>
            ) : (
              <span className="unavailable-link">
                <Icon name="globe" />
                公开 Wiki · 待连接
              </span>
            )}
            <a href="#/settings">
              <Icon name="task" />
              连接与运行状态
            </a>
          </div>
          <p className="sidebar-note">
            <Icon name="lock" size={14} /> 私有数据不进入公开阅读站。
          </p>
        </aside>
        <main className="main" id="main-content" tabIndex={-1}>
          <BrowserAccess api={api} blocked={blocked} />
          <Boundary
            key={
              String(blocked) +
              route.page +
              ":" +
              route.id +
              ":" +
              route.query.toString()
            }
          >
            {blocked ? (
              <section className="panel">
                <Failure error={new WikiApiError("UNAUTHENTICATED", 401)} />
                <p className="muted">
                  会话失效后已隐藏此前读取的私有内容。重试不会创建身份或保存凭据。
                </p>
                <button
                  className="button primary"
                  disabled={!canRetry}
                  onClick={() => {
                    setSearch("");
                    api.retryAccess();
                  }}
                >
                  已完成受信任连接，重新读取
                </button>
              </section>
            ) : (
              page
            )}
          </Boundary>
          <footer className="page-footer">
            个人 Wiki · 私有工作区
            <span>只展示服务已确认的状态，不以示例数据代替真实结果。</span>
          </footer>
        </main>
      </div>
    </Boundary>
  );
}
