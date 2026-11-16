import assert from "node:assert/strict"
import { describe, test } from "node:test"
import type { Html, List, Paragraph, Root, Strong, Text } from "mdast"
import remarkParse from "remark-parse"
import { unified } from "unified"
import { restoreNestedHighlightMarkdown } from "."

function highlightParagraph(value: string): Root {
  return {
    type: "root",
    children: [
      {
        type: "paragraph",
        children: [{ type: "html", value: `<span class="text-highlight">${value}</span>` }],
      },
    ],
  }
}

describe("restoreNestedHighlightMarkdown", () => {
  test("保留高亮中的加粗语义", () => {
    const tree = highlightParagraph("**加粗高亮**")

    restoreNestedHighlightMarkdown(tree)

    const children = (tree.children[0] as Paragraph).children
    assert.equal((children[0] as Html).value, '<span class="text-highlight">')
    assert.equal(children[1]?.type, "strong")
    assert.equal(((children[1] as Strong).children[0] as Text).value, "加粗高亮")
    assert.equal((children[2] as Html).value, "</span>")
  })

  test("保留列表项中高亮与加粗的组合语义", () => {
    const tree: Root = {
      type: "root",
      children: [
        {
          type: "list",
          ordered: false,
          children: [
            {
              type: "listItem",
              children: [
                {
                  type: "paragraph",
                  children: [
                    {
                      type: "html",
                      value: '<span class="text-highlight">**列表加粗高亮**</span>',
                    },
                  ],
                },
              ],
            },
          ],
        },
      ],
    }

    restoreNestedHighlightMarkdown(tree)

    const list = tree.children[0] as List
    const paragraph = list.children[0]?.children[0] as Paragraph
    assert.equal(paragraph.children[1]?.type, "strong")
  })

  test("普通高亮保持普通文本", () => {
    const tree = highlightParagraph("普通高亮")

    restoreNestedHighlightMarkdown(tree)

    const children = (tree.children[0] as Paragraph).children
    assert.equal(children[1]?.type, "text")
    assert.equal((children[1] as Text).value, "普通高亮")
  })

  test("普通加粗不受影响", () => {
    const tree = unified().use(remarkParse).parse("**普通加粗**") as Root

    restoreNestedHighlightMarkdown(tree)

    const paragraph = tree.children[0] as Paragraph
    assert.equal(paragraph.children[0]?.type, "strong")
    assert.equal(((paragraph.children[0] as Strong).children[0] as Text).value, "普通加粗")
  })
})
