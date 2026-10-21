import { EntryProposal, EvidenceReader } from "./material-actions";
import { useState, type FormEvent } from "react";
import { PAGE_SIZE, WikiApiError, type WikiApi } from "./api";
import {
  Badge,
  Empty,
  Field,
  Icon,
  MarkdownBody,
  Notice,
  PageTitle,
  ResourceView,
} from "./components";
import { type Entry, type SearchHit } from "./contracts";
import {
  categories,
  categoryCounts,
  formatTime,
  engineeringEntryHref,
  navigate,
  publication,
  routeHref,
  tags,
  useResource,
} from "./state";

export function HomePage({
  api,
  engineeringUrl,
}: {
  api: WikiApi;
  engineeringUrl: string;
}) {
  const entries = useResource("home-entries", (signal) =>
    api.entries(0, signal),
  );
  const reviews = useResource("home-reviews", (signal) => api.reviews(signal));
  const intakes = useResource("home-intakes", (signal) =>
    api.intakes(0, "", signal),
  );
  return (
    <>
      <PageTitle
        title="我的知识入口"
        description="统一收件、跨领域查阅与知识生长。"
      />
      <div className="home-grid">
        <div>
          <section className="panel">
            <div className="panel-heading">
              <h2>
                <Icon name="book" />
                知识目录
              </h2>
              <a href="#/catalog">进入完整目录 →</a>
            </div>
            <ResourceView resource={entries}>
              {({ data }) => {
                const live = data.filter((e) => !e.deleted);
                const groups = categoryCounts(live);
                return groups.length ? (
                  <>
                    <div className="category-grid">
                      {groups.slice(0, 4).map(({ name, count }, i) => (
                        <a
                          className={"category-card color-" + i}
                          href={routeHref(
                            "catalog",
                            "",
                            new URLSearchParams({ category: name }),
                          )}
                          key={name}
                        >
                          <span className="category-icon">
                            <Icon
                              name={["file", "book", "grid", "globe"][i]}
                              size={25}
                            />
                          </span>
                          <div>
                            <h3>{name}</h3>
                            <p>当前页 {count} 条知识</p>
                          </div>
                          <span>→</span>
                        </a>
                      ))}
                    </div>
                    <p className="muted small">
                      分类来自正式条目元数据。
                      {data.length === PAGE_SIZE
                        ? "仅展示首批 100 条中的分类，请到目录继续翻页。"
                        : "同一条目可属于多个分类，分类计数不相加作为总量。"}
                    </p>
                  </>
                ) : (
                  <Empty title="从第一份知识开始">
                    当前没有可读的正式条目。先收件、预览并明确保留，知识处理完成后会出现在这里。
                  </Empty>
                );
              }}
            </ResourceView>
          </section>
          <section className="panel">
            <div className="panel-heading">
              <h2>
                <Icon name="clock" />
                最近知识修订
              </h2>
              <a href="#/catalog">查看目录 →</a>
            </div>
            <ResourceView resource={entries}>
              {({ data }) => {
                const recent = data
                  .filter((e) => !e.deleted)
                  .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
                  .slice(0, 5);
                return recent.length ? (
                  <>
                    <div className="recent-list">
                      {recent.map((entry) => (
                        <a
                          key={entry.entry_id}
                          href={routeHref("entry", entry.entry_id)}
                        >
                          <span className="file-icon">
                            <Icon name="file" />
                          </span>
                          <span>
                            <strong>{entry.title}</strong>
                            <small>
                              {categories(entry).join(" · ")} · 修订{" "}
                              {entry.revision}
                            </small>
                          </span>
                          <time>{formatTime(entry.updated_at)}</time>
                          <span>›</span>
                        </a>
                      ))}
                    </div>
                    <p className="muted small">
                      按当前载入目录的更新时间排序，不把排序结果推断为新增或掌握度。
                    </p>
                  </>
                ) : (
                  <p className="muted">暂无正式知识修订。</p>
                );
              }}
            </ResourceView>
          </section>
          <section className="panel">
            <div className="panel-heading">
              <h2>
                <Icon name="grid" />
                能力入口
              </h2>
            </div>
            <div className="capability-grid">
              <a href="#/tools">
                <Icon name="grid" />
                <strong>工具与 Skill</strong>
                <small>后续规划，尚未提供安装</small>
              </a>
              <a href="#/tasks">
                <Icon name="task" />
                <strong>后台任务</strong>
                <small>按任务 ID 查真实进度</small>
              </a>
              <a href="#/settings">
                <Icon name="settings" />
                <strong>连接与运行状态</strong>
                <small>只读检查服务状态</small>
              </a>
            </div>
          </section>
        </div>
        <div>
          <section className="panel">
            <div className="panel-heading">
              <h2>
                <Icon name="inbox" />
                私有收件
              </h2>
              <a href="#/inbox">打开 →</a>
            </div>
            <p className="muted">
              把链接送入临时队列，再查看来源完整度并决定是否保留。
            </p>
            <ResourceView resource={intakes}>
              {({ data }) =>
                data.items.length ? (
                  <>
                    <p className="muted small">
                      本次读取 {data.items.length} 项
                      {data.has_more ? "，还有更多" : ""}，不作为待处理总量。
                    </p>
                    {data.items.slice(0, 3).map((item) => (
                      <a
                        className="review-mini"
                        href={routeHref("intake", item.intake_id)}
                        key={item.intake_id}
                      >
                        <strong>{item.title || "待查看来源"}</strong>
                        <Badge>{item.state}</Badge>
                      </a>
                    ))}
                  </>
                ) : (
                  <p className="muted">当前没有收件记录。</p>
                )
              }
            </ResourceView>
            <a className="button primary full" href="#/inbox">
              查看收件与投递链接
            </a>
          </section>
          <section className="panel">
            <div className="panel-heading">
              <h2>
                <Icon name="star" />
                需要本人确认
              </h2>
              <a href="#/reviews">查看全部 →</a>
            </div>
            <ResourceView resource={reviews}>
              {({ data }) =>
                data.length ? (
                  <>
                    <p className="muted">
                      已读取 {data.length} 项待确认
                      {data.length === PAGE_SIZE ? "（已达本次读取上限）" : ""}
                      。
                    </p>
                    {data.slice(0, 2).map((change) => (
                      <a
                        className="review-mini"
                        href={routeHref("review", change.change_id)}
                        key={change.change_id}
                      >
                        <strong>{change.title}</strong>
                        <Badge tone="warning">待本人决定</Badge>
                      </a>
                    ))}
                  </>
                ) : (
                  <p className="muted">当前没有需要本人确认的提案。</p>
                )
              }
            </ResourceView>
          </section>
          <section className="learning-link">
            <Icon name="branch" size={31} />
            <h2>知枝 · 工程学习</h2>
            <p>从工程变更进入学习与自己的理解。通用知识仍在个人 Wiki 管理。</p>
            {engineeringUrl ? (
              <a
                className="button primary"
                href={engineeringUrl}
                rel="noreferrer"
              >
                去知枝 ↗
              </a>
            ) : (
              <Notice title="工程学习入口待连接">
                知枝目前是独立服务，宿主尚未提供经过验证的跳转入口。不会用占位链接假装已接入。
              </Notice>
            )}
          </section>
        </div>
      </div>
    </>
  );
}

export function CatalogPage({
  api,
  query,
}: {
  api: WikiApi;
  query: URLSearchParams;
}) {
  const q = query.get("q") || "";
  const page = Math.max(
    0,
    Math.min(100000, Math.floor(Number(query.get("page")) || 0)),
  );
  const category = query.get("category") || "";
  const [input, setInput] = useState(q);
  const [selected, setSelected] = useState("");
  const data = useResource<{ entries?: Entry[]; hits?: SearchHit[] }>(
    "catalog:" + q + ":" + page,
    async (signal) =>
      q
        ? { hits: (await api.search(q, signal)).data }
        : { entries: (await api.entries(page * PAGE_SIZE, signal)).data },
  );
  function search(event: FormEvent) {
    event.preventDefault();
    const params = new URLSearchParams();
    if (input.trim()) params.set("q", input.trim());
    navigate(routeHref("catalog", "", params));
  }
  function filter(name: string) {
    const params = new URLSearchParams(query);
    name ? params.set("category", name) : params.delete("category");
    navigate(routeHref("catalog", "", params));
  }
  function move(delta: number) {
    const params = new URLSearchParams(query);
    params.set("page", String(page + delta));
    navigate(routeHref("catalog", "", params));
  }
  return (
    <>
      <PageTitle
        title="知识目录与全库检索"
        description="按主题组织正式知识，查询来源与版本。工程、设计、学习方法与生活知识保持同一入口。"
      />
      <form className="catalog-search panel" role="search" onSubmit={search}>
        <Icon name="search" />
        <input
          aria-label="检索词"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          maxLength={500}
          placeholder="搜索知识标题、正文、标签…"
        />
        <button className="button primary">搜索</button>
        {q && (
          <a className="button" href="#/catalog">
            清除检索
          </a>
        )}
      </form>
      <ResourceView resource={data}>
        {(result) => {
          const all = (result.entries || []).filter((e) => !e.deleted);
          const shown = category
            ? all.filter((e) => categories(e).includes(category))
            : all;
          const hits = result.hits;
          const active = shown.find((e) => e.entry_id === selected) || shown[0];
          return (
            <div className="catalog-grid">
              <aside className="panel category-tree">
                <h2>
                  <Icon name="book" />
                  知识目录
                </h2>
                {hits ? (
                  <Notice>
                    全文检索跨当前用户可读的正式知识。来源原件和学习原话不混入此搜索范围。
                  </Notice>
                ) : (
                  <>
                    <button
                      className={!category ? "selected" : ""}
                      onClick={() => filter("")}
                    >
                      全部知识 <span>{all.length}</span>
                    </button>
                    {categoryCounts(all).map((item) => (
                      <button
                        key={item.name}
                        className={category === item.name ? "selected" : ""}
                        onClick={() => filter(item.name)}
                      >
                        <Icon name="file" size={17} />
                        {item.name}
                        <span>{item.count}</span>
                      </button>
                    ))}
                    <p className="muted small">
                      分类计数仅针对本页已读取条目。没有分类时显示“未分类”，不自动生成分类。
                    </p>
                  </>
                )}
              </aside>
              <section className="panel results-panel">
                <div className="results-meta">
                  <span>
                    {hits
                      ? "检索结果 " + hits.length + " 条"
                      : "本页 " + shown.length + " 条知识"}
                  </span>
                  <Badge>
                    {hits ? "正式知识全文检索" : "第 " + (page + 1) + " 页"}
                  </Badge>
                </div>
                {hits?.some((h) => h.stale_index) && (
                  <Notice tone="warning" title="索引已落后">
                    部分结果不是最新正式版本，请打开条目回源核对。
                  </Notice>
                )}
                {hits ? (
                  hits.length ? (
                    <div className="search-results">
                      {hits.map((hit) => (
                        <a
                          key={hit.entry_id}
                          href={routeHref("entry", hit.entry_id)}
                        >
                          <h2>{hit.title}</h2>
                          <p>{hit.snippet}</p>
                          <small>
                            修订 {hit.revision}
                            {hit.matched_section
                              ? " · " + hit.matched_section
                              : ""}
                          </small>
                          {hit.stale_index && (
                            <Badge tone="warning">索引落后</Badge>
                          )}
                        </a>
                      ))}
                    </div>
                  ) : (
                    <Empty title="库内没有找到匹配结果">
                      请换一个检索词；不会用生成内容冒充已存在知识。
                    </Empty>
                  )
                ) : shown.length ? (
                  <div className="entry-list" role="list">
                    {shown.map((entry) => (
                      <div
                        className={
                          "entry-row" +
                          (active?.entry_id === entry.entry_id
                            ? " selected"
                            : "")
                        }
                        role="listitem"
                        key={entry.entry_id}
                      >
                        <button
                          className="row-preview"
                          aria-label={"预览 " + entry.title}
                          onClick={() => setSelected(entry.entry_id)}
                        >
                          <Icon name="file" />
                        </button>
                        <a
                          className="entry-row-main"
                          href={routeHref("entry", entry.entry_id)}
                        >
                          <strong>{entry.title}</strong>
                          <span className="tags">
                            {tags(entry)
                              .slice(0, 3)
                              .map((tag) => (
                                <span key={tag}>{tag}</span>
                              ))}
                          </span>
                        </a>
                        <span className="entry-version">v{entry.revision}</span>
                        <time>
                          {entry.updated_at
                            ? formatTime(entry.updated_at)
                            : "时间未提供"}
                        </time>
                      </div>
                    ))}
                  </div>
                ) : (
                  <Empty
                    title={
                      category ? "本页没有该分类的条目" : "尚无正式知识条目"
                    }
                  >
                    目录只显示服务返回的正式知识，不会装载设计稿里的示例内容。
                  </Empty>
                )}
                {!hits && (
                  <div className="pagination">
                    <button
                      className="button"
                      disabled={page === 0}
                      onClick={() => move(-1)}
                    >
                      上一页
                    </button>
                    <span>每次最多 {PAGE_SIZE} 条</span>
                    <button
                      className="button"
                      disabled={(result.entries?.length || 0) < PAGE_SIZE}
                      onClick={() => move(1)}
                    >
                      下一页
                    </button>
                  </div>
                )}
                {hits && hits.length === PAGE_SIZE && (
                  <Notice>已达到本次检索上限 100 条，请缩小检索范围。</Notice>
                )}
              </section>
              {!hits && (
                <aside className="panel entry-preview">
                  {active ? (
                    <>
                      <div className="preview-title">
                        <Icon name="file" size={27} />
                        <h2>{active.title}</h2>
                      </div>
                      <div className="tags">
                        {tags(active).map((tag) => (
                          <span key={tag}>{tag}</span>
                        ))}
                      </div>
                      <h3>正文片段</h3>
                      <p className="excerpt">
                        {active.body.slice(0, 400) || "正文为空。"}
                      </p>
                      <h3>来源与修订</h3>
                      <dl>
                        <Field label="关联证据">
                          {active.evidence_refs.length} 项
                        </Field>
                        <Field label="当前修订">v{active.revision}</Field>
                        <Field label="可见性意图">
                          {publication(active).intent === "public"
                            ? "希望公开"
                            : "私有或未明确"}
                        </Field>
                        <Field label="公开资格">
                          {publication(active).eligible}
                        </Field>
                      </dl>
                      <Notice>公开意图与实际发布不同，本页不进行发布。</Notice>
                      <a
                        className="button primary full"
                        href={routeHref("entry", active.entry_id)}
                      >
                        打开完整条目 →
                      </a>
                    </>
                  ) : (
                    <Empty title="选取条目查看详情" />
                  )}
                </aside>
              )}
            </div>
          );
        }}
      </ResourceView>
    </>
  );
}

export function EntryPage({
  api,
  id,
  query,
  engineeringUrl,
}: {
  api: WikiApi;
  id: string;
  query: URLSearchParams;
  engineeringUrl: string;
}) {
  const rawRevision = Number(query.get("revision"));
  const invalidRevision =
    query.has("revision") &&
    (!/^[1-9][0-9]*(?![\s\S])/.test(query.get("revision") || "") ||
      !Number.isSafeInteger(rawRevision));
  const revision =
    Number.isSafeInteger(rawRevision) && rawRevision > 0
      ? rawRevision
      : undefined;
  const resource = useResource(
    "entry:" + id + ":" + query.get("revision"),
    (signal) =>
      invalidRevision
        ? Promise.reject(new WikiApiError("INVALID_INPUT", 422))
        : api.entry(id, revision, signal),
  );
  const [version, setVersion] = useState(revision ? String(revision) : "");
  const [tab, setTab] = useState("body");
  function selectRevision(event: FormEvent) {
    event.preventDefault();
    const value = Number(version);
    if (!Number.isSafeInteger(value) || value < 1) return;
    navigate(
      routeHref("entry", id, new URLSearchParams({ revision: String(value) })),
    );
  }
  return (
    <>
      <a className="back-link" href="#/catalog">
        ← 知识目录
      </a>
      <ResourceView resource={resource}>
        {({ data: entry }) => (
          <>
            <PageTitle
              title={entry.title}
              description={"正式知识条目 · " + entry.entry_id}
              actions={
                <div className="actions">
                  <Badge tone={entry.deleted ? "danger" : "success"}>
                    {entry.deleted
                      ? "已删除的历史条目"
                      : "修订 " + entry.revision}
                  </Badge>
                  <button className="button" onClick={resource.reload}>
                    重新读取（清除未提交编辑）
                  </button>
                </div>
              }
            />
            {entry.deleted && (
              <Notice tone="warning">
                这是已删除条目的可读历史，不代表当前有效知识。
              </Notice>
            )}
            <div className="article-grid">
              <article className="panel article-panel">
                <div className="article-meta">
                  <span className="tags">
                    {tags(entry).map((tag) => (
                      <span key={tag}>{tag}</span>
                    ))}
                  </span>
                  <span>更新：{formatTime(entry.updated_at)}</span>
                </div>
                <div className="tabs" role="tablist" aria-label="条目内容">
                  <button
                    role="tab"
                    aria-selected={tab === "body"}
                    onClick={() => setTab("body")}
                  >
                    正文
                  </button>
                  <button
                    role="tab"
                    aria-selected={tab === "sources"}
                    onClick={() => setTab("sources")}
                  >
                    来源与证据（{entry.evidence_refs.length}）
                  </button>
                </div>
                {tab === "body" ? (
                  <section role="tabpanel">
                    <MarkdownBody body={entry.body} />
                    <p className="muted small">
                      以服务返回的正式版本为准。外部图片、HTML
                      和媒体不会自动载入。
                    </p>
                  </section>
                ) : (
                  <section role="tabpanel">
                    <h2>来源与原始材料</h2>
                    <Notice>
                      来源原件与正式正文分开。原件、学习原话和私人附言不会因条目公开意图而自动公开。
                    </Notice>
                    {entry.evidence_refs.length ? (
                      <div className="source-list">
                        {entry.evidence_refs.map((ref, index) => (
                          <EvidenceReader
                            key={JSON.stringify(ref) + index}
                            api={api}
                            reference={ref}
                          />
                        ))}
                      </div>
                    ) : (
                      <p className="muted">服务未返回来源引用。</p>
                    )}
                  </section>
                )}
              </article>
              <aside>
                <section className="panel">
                  <h2>
                    <Icon name="clock" />
                    版本与历史
                  </h2>
                  <dl>
                    <Field label="正在阅读">
                      v{entry.revision}
                      {revision ? " · 指定历史版本" : " · 最新读取"}
                    </Field>
                    <Field label="最近更新">
                      {formatTime(entry.updated_at)}
                    </Field>
                    <Field label="创建时间">
                      {formatTime(entry.created_at)}
                    </Field>
                  </dl>
                  <form onSubmit={selectRevision} className="version-form">
                    <label htmlFor="revision">读取指定修订</label>
                    <input
                      id="revision"
                      type="number"
                      min="1"
                      step="1"
                      value={version}
                      onChange={(e) => setVersion(e.target.value)}
                      placeholder="修订号"
                    />
                    <button
                      className="button"
                      disabled={!version || Number(version) < 1}
                    >
                      读取
                    </button>
                  </form>
                  {revision && (
                    <a className="button full" href={routeHref("entry", id)}>
                      返回当前版本
                    </a>
                  )}
                </section>
                <section className="panel">
                  <h2>
                    <Icon name="globe" />
                    公开边界
                  </h2>
                  <dl>
                    <Field label="公开意图">
                      {publication(entry).intent === "public"
                        ? "希望公开"
                        : "私有或未明确"}
                    </Field>
                    <Field label="公开资格">
                      {publication(entry).eligible}
                    </Field>
                    <Field label="实际发布">
                      {publication(entry).published}
                    </Field>
                  </dl>
                  <Notice>
                    资格通过不等于已构建、已部署。公开阅读站只使用独立合格投影。
                  </Notice>
                </section>
                <section className="panel">
                  <h2>修订与学习</h2>
                  <p>修改与删除提案位于正文下方，须经过独立复核。</p>
                  <a className="button" href="#/reviews">
                    查看重要确认
                  </a>
                  {!entry.deleted &&
                  engineeringEntryHref(
                    engineeringUrl,
                    entry.entry_id,
                    entry.revision,
                  ) ? (
                    <a
                      className="text-link"
                      href={engineeringEntryHref(
                        engineeringUrl,
                        entry.entry_id,
                        entry.revision,
                      )}
                      rel="noreferrer"
                    >
                      用当前修订继续工程学习 ↗
                    </a>
                  ) : (
                    <p className="muted small">
                      工程学习入口尚未连接，或该条目已删除。
                    </p>
                  )}
                  <small className="muted">
                    只传递条目 ID 与当前所读修订 {entry.revision}
                    。知枝会重新授权读取；选择已有卡并明确确认后才建立引用，不自动创建卡片。
                  </small>
                </section>
              </aside>
            </div>
            <section className="panel">
              <h2>版本化修改与删除提案</h2>
              <EntryProposal
                key={entry.entry_id + ":" + entry.revision}
                api={api}
                entry={entry}
                historical={!!revision}
              />
            </section>
          </>
        )}
      </ResourceView>
    </>
  );
}
