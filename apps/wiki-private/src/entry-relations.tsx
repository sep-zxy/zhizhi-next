import { useState } from "react";
import type { WikiApi } from "./api";
import { Badge, Empty, Icon, Notice, ResourceView } from "./components";
import type { Entry } from "./contracts";
import type { RelationKind } from "./relation-contracts";
import { EvidenceReader } from "./material-actions";
import { routeHref, useResource } from "./state";

const labels: Record<RelationKind, string> = {
  navigation: "导航关联",
  confirmed: "证据已确认",
  candidate: "候选 · 待核验",
};
export function EntryRelations({ api, entry }: { api: WikiApi; entry: Entry }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="panel">
      <h2>
        <Icon name="branch" /> 相关知识
      </h2>
      <p className="muted small">
        只读查看直接相邻关系。导航不是事实，候选不是确认，不推断掌握度。
      </p>
      <button
        className="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? "收起局部关系" : "查看局部关系"}
      </button>
      {open && (
        <RelationContent
          key={entry.entry_id + ":" + entry.revision}
          api={api}
          entry={entry}
        />
      )}
    </section>
  );
}
function RelationContent({ api, entry }: { api: WikiApi; entry: Entry }) {
  const resource = useResource(
    "relations:" + entry.entry_id + ":" + entry.revision,
    (signal) => api.relations(entry.entry_id, signal),
  );
  return (
    <ResourceView resource={resource}>
      {({ data }) => {
        const root = data.nodes.find(
          (node) => node.entry_id === entry.entry_id,
        )!;
        if (root.revision !== entry.revision)
          return (
            <Notice tone="warning" title="关系版本与正在阅读的版本不同">
              当前关系服务对应 v{root.revision}，正在阅读 v{entry.revision}
              ；不把当前关系冒充历史版本的关系。请重新读取当前条目后再核对。
            </Notice>
          );
        return (
          <>
            <p className="muted small">
              当前直接关系 {data.edges.length} 项，最多读取 20 项。
              {data.truncated ? "结果已截断，不代表全部关系。" : ""}
            </p>
            {data.edges.length ? (
              data.edges.map((edge) => {
                const outgoing = edge.source_entry_id === entry.entry_id;
                const other = data.nodes.find(
                  (node) =>
                    node.entry_id ===
                    (outgoing ? edge.target_entry_id : edge.source_entry_id),
                )!;
                return (
                  <section className="relation-card" key={edge.edge_id}>
                    <Badge
                      tone={
                        edge.kind === "confirmed"
                          ? "success"
                          : edge.kind === "candidate"
                            ? "warning"
                            : "info"
                      }
                    >
                      {labels[edge.kind]}
                    </Badge>
                    <a
                      className="text-link"
                      href={routeHref(
                        "entry",
                        other.entry_id,
                        new URLSearchParams({
                          revision: String(other.revision),
                        }),
                      )}
                    >
                      {outgoing ? "→" : "←"} {other.title} · v{other.revision}
                    </a>
                    {edge.statement && <p>{edge.statement}</p>}
                    <small className="muted">
                      {edge.relation_type} ·{" "}
                      {outgoing ? "指向关联条目" : "来自关联条目"}
                    </small>
                    {edge.evidence_refs.map((reference, index) => (
                      <EvidenceReader
                        key={edge.edge_id + index}
                        api={api}
                        reference={reference}
                      />
                    ))}
                  </section>
                );
              })
            ) : (
              <Empty title="当前没有可读的直接关系">
                不根据标题或相似内容自动画出已确认关系。
              </Empty>
            )}
            <details>
              <summary>关系投影版本</summary>
              <code>{data.projection_revision}</code>
            </details>
          </>
        );
      }}
    </ResourceView>
  );
}
