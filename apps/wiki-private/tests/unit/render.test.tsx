import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MarkdownBody, SafeExternal } from "../../src/components";
import { ToolsPage } from "../../src/workflow-pages";
describe("safe source and planning rendering", () => {
  it("escapes untrusted HTML and does not load remote media", () => {
    const html = renderToStaticMarkup(
      <MarkdownBody
        body={
          '# 标题\n\n<script>alert(1)</script>\n![tracking](https://example.com/tracker.png)\n<iframe src="https://evil.test" />'
        }
      />,
    );
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<iframe");
    expect(html).toContain("&lt;script&gt;");
  });
  it("does not create executable source links", () => {
    for (const url of [
      "javascript:alert(1)",
      "data:text/html,hi",
      "https://u:p@example.com",
    ])
      expect(
        renderToStaticMarkup(<SafeExternal url={url}>来源</SafeExternal>),
      ).not.toContain("href=");
  });
  it("removes opener and referrer on intentional source navigation", () => {
    const html = renderToStaticMarkup(
      <SafeExternal url="https://example.com/article">来源</SafeExternal>,
    );
    expect(html).toContain('rel="noopener noreferrer"');
  });
  it("does not offer arbitrary skill installation", () => {
    const html = renderToStaticMarkup(<ToolsPage />);
    expect(html).toContain("尚未实现");
    expect(html).not.toContain("<button");
    expect(html).toContain("不能自动变成可执行 Skill");
  });
});

describe("private article safe structured reading", () => {
  it("renders actual bullets, ordered steps and emphasis without executing markup", () => {
    const html = renderToStaticMarkup(
      <MarkdownBody
        body={
          '## 机制\n- **单写者**：使用 `CAS`\n- <img src="https://evil.test/x" />\n\n3. 核对\n4. 提交\n\n```\n- not a list\n```'
        }
      />,
    );
    expect(html).toContain("<ul>");
    expect(html).toContain("<strong>单写者</strong>");
    expect(html).toContain("<code>CAS</code>");
    expect(html).toContain('<ol start="3">');
    expect(html).toContain("<pre><code>- not a list</code></pre>");
    expect(html).not.toContain("<img");
  });
  it("keeps remote links and images as inert text", () => {
    const html = renderToStaticMarkup(
      <MarkdownBody
        body={
          "- [go](javascript:alert(1))\n- ![image](https://tracker.test/x)\n- **<script>bad</script>**"
        }
      />,
    );
    expect(html).not.toContain("href=");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });
});
