import { fields, text, type EvidenceRef } from "./contracts";

export type RelationKind = "navigation" | "confirmed" | "candidate";
export interface RelationNode {
  entry_id: string;
  revision: number;
  title: string;
}
export interface RelationEdge {
  edge_id: string;
  source_entry_id: string;
  target_entry_id: string;
  source_revision: number;
  target_revision: number;
  kind: RelationKind;
  statement: string;
  relation_type: string;
  evidence_refs: EvidenceRef[];
}
export interface RelationView {
  root_entry_id: string;
  nodes: RelationNode[];
  edges: RelationEdge[];
  projection_revision: string;
  truncated: boolean;
}
function required(value: unknown): string {
  if (typeof value !== "string" || !value) throw Error("INVALID_RESPONSE");
  return value;
}
function revision(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1)
    throw Error("INVALID_RESPONSE");
  return value;
}
/** Validate a bounded direct-neighbor projection; never upgrade a candidate locally. */
export function parseRelationView(raw: unknown, root: string): RelationView {
  const value = fields(raw);
  if (
    value.root_entry_id !== root ||
    value.inference !== "none" ||
    !Array.isArray(value.nodes) ||
    !Array.isArray(value.edges) ||
    value.nodes.length > 21 ||
    value.edges.length > 20 ||
    typeof value.truncated !== "boolean"
  )
    throw Error("INVALID_RESPONSE");
  const nodes = value.nodes.map((rawNode) => {
    const node = fields(rawNode);
    return {
      entry_id: required(node.entry_id),
      revision: revision(node.revision),
      title: required(node.title),
    };
  });
  const byId = new Map(nodes.map((node) => [node.entry_id, node]));
  if (!byId.has(root) || byId.size !== nodes.length)
    throw Error("INVALID_RESPONSE");
  const edges = value.edges.map((rawEdge) => {
    const edge = fields(rawEdge),
      kind = edge.kind;
    if (
      !["navigation", "confirmed", "candidate"].includes(String(kind)) ||
      edge.is_fact !== (kind === "confirmed")
    )
      throw Error("INVALID_RESPONSE");
    const source = byId.get(required(edge.source_entry_id)),
      target = byId.get(required(edge.target_entry_id));
    if (
      !source ||
      !target ||
      (source.entry_id !== root && target.entry_id !== root) ||
      revision(edge.source_revision) !== source.revision ||
      revision(edge.target_revision) !== target.revision
    )
      throw Error("INVALID_RESPONSE");
    const refs = edge.evidence_refs === undefined ? [] : edge.evidence_refs;
    if (!Array.isArray(refs) || refs.length > 50)
      throw Error("INVALID_RESPONSE");
    const evidence_refs = refs.map((rawRef): EvidenceRef => {
      const ref = fields(rawRef);
      if (Boolean(ref.source_id) === Boolean(ref.learning_outcome_id))
        throw Error("INVALID_RESPONSE");
      return {
        ...(ref.source_id
          ? { source_id: required(ref.source_id) }
          : { learning_outcome_id: required(ref.learning_outcome_id) }),
        content_hash: required(ref.content_hash),
        ...(text(fields(ref.locator).quote)
          ? { locator: { quote: text(fields(ref.locator).quote) } }
          : {}),
      };
    });
    if (
      kind === "confirmed" &&
      (edge.verification !== "reviewed_evidence" ||
        !evidence_refs.length ||
        edge.declared_target_revision !== target.revision ||
        !text(fields(edge.review_provenance).review_id))
    )
      throw Error("INVALID_RESPONSE");
    return {
      edge_id: required(edge.edge_id),
      source_entry_id: source.entry_id,
      target_entry_id: target.entry_id,
      source_revision: source.revision,
      target_revision: target.revision,
      kind: kind as RelationKind,
      statement: text(edge.statement),
      relation_type: text(edge.relation_type),
      evidence_refs,
    };
  });
  if (new Set(edges.map((edge) => edge.edge_id)).size !== edges.length)
    throw Error("INVALID_RESPONSE");
  return {
    root_entry_id: root,
    nodes,
    edges,
    projection_revision: required(value.projection_revision),
    truncated: value.truncated,
  };
}
