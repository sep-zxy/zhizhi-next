import type { Html, Parent, PhrasingContent, Root } from "mdast"
import remarkParse from "remark-parse"
import { unified } from "unified"
import { SKIP, visit } from "unist-util-visit"
import type { QuartzTransformerPlugin } from "../types"

const highlightHtmlPattern = /^<span class="text-highlight">([\s\S]*)<\/span>$/

function parseInlineMarkdown(markdown: string): PhrasingContent[] | undefined {
  const parsed = unified().use(remarkParse).parse(markdown) as Root
  if (parsed.children.length !== 1 || parsed.children[0]?.type !== "paragraph") {
    return undefined
  }

  return parsed.children[0].children
}

/**
 * ObsidianFlavoredMarkdown currently serializes highlight contents into one raw HTML node.
 * Reparse the inline contents so combinations such as ==**bold highlight**== keep their
 * nested Markdown semantics instead of showing the literal ** delimiters.
 */
export function restoreNestedHighlightMarkdown(tree: Root): void {
  visit(tree, "html", (node: Html, index: number | undefined, parent: Parent | undefined) => {
    if (parent == null || index == null) return

    const match = highlightHtmlPattern.exec(node.value)
    if (match == null) return

    const children = parseInlineMarkdown(match[1] ?? "")
    if (children == null) return

    const replacement: PhrasingContent[] = [
      { type: "html", value: '<span class="text-highlight">' },
      ...children,
      { type: "html", value: "</span>" },
    ]
    parent.children.splice(index, 1, ...replacement)
    return [SKIP, index + replacement.length]
  })
}

export const NestedMarkdownHighlights: QuartzTransformerPlugin = () => ({
  name: "NestedMarkdownHighlights",
  markdownPlugins() {
    return [() => restoreNestedHighlightMarkdown]
  },
})
