import { pathToRoot, joinSegments } from "../../util/path"
import type { FullSlug } from "../../util/path"
import type { PageFrame } from "./types"
import {
  portalEntries,
  publicGroups,
  readPublicManifest,
  recentPublicEntries,
} from "./publicPortal"
import style from "./publicWikiStyle"

export const PublicWikiFrame: PageFrame = {
  name: "public-wiki",
  css: style,
  render({ componentData: data, left, pageBody: Content, right }) {
    const manifest = readPublicManifest(data.ctx.argv.directory)
    const entries = portalEntries(data.allFiles, manifest)
    const groups = publicGroups(entries)
    const slug = String(data.fileData.slug)
    const home = slug === "index"
    const current = entries.find((entry) => entry.slug === slug)
    const base = pathToRoot(data.fileData.slug as FullSlug)
    const href = (value: string) => joinSegments(base, value)
    const categoryId = (tag: string) => "category-" + encodeURIComponent(tag)
    const recent = recentPublicEntries(entries)
    const related = current
      ? entries.filter(
          (entry) =>
            entry.slug !== slug &&
            (entry.links.includes(slug) || current.links.includes(entry.slug)),
        )
      : []
    const directoryPage = slug.startsWith("tags/") || slug.endsWith("/index")
    if (!home && !current && !directoryPage)
      throw new Error("Page is outside the approved public manifest")
    return (
      <>
        <a class="public-skip" href="#public-main">
          跳到正文
        </a>
        <header class="public-header">
          <a href={href("index")} class="public-brand">
            <span aria-hidden="true">▱</span> 个人 Wiki <small>公开知识库</small>
          </a>
          <div class="public-search">
            {left.map((Component) => (
              <Component {...data} />
            ))}
          </div>
          <a class="public-about-link" href={href("index") + "#about"}>
            关于本站
          </a>
        </header>
        <div class="public-trail">
          <a href={href("index")}>⌂ 首页</a>
          <span>只读公开知识 · Markdown 自动生成</span>
        </div>
        <div class="public-layout">
          <nav class="public-nav" aria-label="公开 Wiki 导航">
            <h2>导航</h2>
            <a href={href("index")} aria-current={home ? "page" : undefined}>
              ⌂　首页
            </a>
            <a href={href("index") + "#categories"}>▦　分类</a>
            <a href={href("index") + "#all-entries"}>▤　所有条目</a>
            <a href={href("index") + "#recent"}>◷　最近修订</a>
            <a href={href("index") + "#about"}>ⓘ　关于</a>
            <h2>知识分类</h2>
            {groups.map(([tag]) => (
              <a
                href={href("index") + "#" + categoryId(tag)}
                aria-current={current?.tags.includes(tag) ? "location" : undefined}
              >
                ◇　{tag}
              </a>
            ))}
            <p class="public-readonly">只读公开内容</p>
          </nav>
          <main id="public-main" class="public-main">
            {home ? (
              <>
                <h1>公开知识库</h1>
                <p class="public-subtitle">技术与工程专题 · 来自知枝的公开沉淀</p>
                <p class="public-intro">将允许公开的知识整理为可检索、可追溯的条目。</p>
                <section id="categories">
                  <h2>按主题浏览</h2>
                  <div class="public-categories">
                    {groups.map(([tag, members]) => (
                      <section id={categoryId(tag)}>
                        <h3>
                          {tag}
                          <small>{members.length} 条</small>
                        </h3>
                        <ul>
                          {members.map((entry) => (
                            <li>
                              <a href={href(entry.slug)}>{entry.title}</a>
                            </li>
                          ))}
                        </ul>
                      </section>
                    ))}
                  </div>
                  {!groups.length && (
                    <p class="public-empty">
                      暂无合格公开条目。内容通过公开审核后，这里会自动生成分类和条目。
                    </p>
                  )}
                </section>
                <section id="all-entries">
                  <h2>
                    所有条目 <small>{entries.length}</small>
                  </h2>
                  {entries.map((entry) => (
                    <div class="public-entry-row">
                      <a href={href(entry.slug)}>▤　{entry.title}</a>
                      {entry.description && <p>{entry.description}</p>}
                    </div>
                  ))}
                  {!entries.length && <p class="public-empty">公开目录为空。</p>}
                </section>
                <section id="about">
                  <h2>关于本站</h2>
                  <p>
                    这是只读公开知识库。分类、条目、来源与关联均由已允许公开的 Markdown
                    投影生成，页面不包含私有工作区、学习原话、账号或工具权限信息。
                  </p>
                </section>
              </>
            ) : (
              <>
                <div class="public-article-path">
                  <a href={href("index")}>首页</a> / {current?.tags.join(" / ") || "知识目录"}
                </div>
                <h1>{current?.title ?? data.fileData.frontmatter?.title}</h1>
                {current && (
                  <p class="public-meta">
                    {current.tags.join(" · ")} · 公开条目 ·{" "}
                    {Date.parse(current.updated) > 0 ? (
                      <>
                        内容修订：
                        <time dateTime={current.updated}>{current.updated.slice(0, 10)}</time>
                      </>
                    ) : (
                      "内容修订时间未提供"
                    )}
                  </p>
                )}
                <Content {...data} />
              </>
            )}
          </main>
          <aside class="public-rail" aria-label={home ? "最近修订与阅读提示" : "文章目录与关联"}>
            {home ? (
              <>
                <section id="recent">
                  <h2>最近修订</h2>
                  {recent.map((entry) => (
                    <div class="public-revision">
                      <time dateTime={entry.updated}>{entry.updated.slice(0, 10)}</time>
                      <a href={href(entry.slug)}>{entry.title}</a>
                      {entry.description && <p>{entry.description}</p>}
                    </div>
                  ))}
                  {!recent.length && <p>暂无可用的内容修订记录。</p>}
                </section>
                <section>
                  <h2>阅读提示</h2>
                  <p>这里展示公开审核后的知识条目。阅读条目时，可沿来源与相关条目继续查阅。</p>
                  <p>修订日期来自知识内容，不是网站构建时间。</p>
                </section>
              </>
            ) : (
              <>
                {right.map((Component) => (
                  <Component {...data} />
                ))}
                <section>
                  <h2>相关条目与反链</h2>
                  {related.length ? (
                    related.map((entry) => (
                      <p>
                        <a href={href(entry.slug)}>▤ {entry.title}</a>
                        {entry.links.includes(slug) && <small>　引用本页</small>}
                      </p>
                    ))
                  ) : (
                    <p>暂无公开关联条目。</p>
                  )}
                </section>
              </>
            )}
          </aside>
        </div>
        <footer class="public-footer">
          公开页面由允许公开的 Markdown 确定性生成。<span>只读公开视图</span>
        </footer>
      </>
    )
  },
}
