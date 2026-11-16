import { z } from 'zod';
import { apiFetch } from './client';
export const relationKinds = ['navigation', 'confirmed', 'candidate'] as const;
export type RelationKind = (typeof relationKinds)[number];
export const relationLabels: Record<RelationKind, string> = {
  navigation: '导航关联',
  confirmed: '证据确认',
  candidate: 'AI 候选',
};
const node = z.object({
  entry_id: z.string().min(1),
  revision: z.number().int().positive(),
  title: z.string(),
});
const edge = z.object({
  edge_id: z.string(),
  source_entry_id: z.string(),
  target_entry_id: z.string(),
  source_revision: z.number().int().positive(),
  target_revision: z.number().int().positive(),
  kind: z.enum(relationKinds),
  is_fact: z.boolean(),
  statement: z.string().optional(),
  verification: z.string().optional(),
  declared_target_revision: z.number().int().nullable().optional(),
  evidence_refs: z.array(z.record(z.string(), z.unknown())).optional(),
  review_provenance: z.record(z.string(), z.unknown()).nullable().optional(),
});
const evidenceIdentity = z
  .object({
    source_id: z.string().trim().min(1).optional(),
    learning_outcome_id: z.string().trim().min(1).optional(),
    content_hash: z.string().trim().min(1),
  })
  .refine(
    (ref) =>
      Number(Boolean(ref.source_id)) +
        Number(Boolean(ref.learning_outcome_id)) ===
      1,
  );
const reviewIdentity = z.object({ review_id: z.string().trim().min(1) });
const projection = z
  .object({
    nodes: z.array(node),
    edges: z.array(edge),
    projection_revision: z.string(),
    root_entry_id: z.string(),
    truncated: z.boolean(),
    inference: z.literal('none'),
    visibility: z.enum(['all', 'public']),
  })
  .superRefine((value, ctx) => {
    const nodes = new Map(
      value.nodes.map((item) => [item.entry_id, item.revision]),
    );
    if (nodes.size !== value.nodes.length)
      ctx.addIssue({ code: 'custom', message: 'Duplicate nodes' });
    if (!nodes.has(value.root_entry_id))
      ctx.addIssue({ code: 'custom', message: 'Missing root' });
    for (const item of value.edges) {
      if (
        nodes.get(item.source_entry_id) !== item.source_revision ||
        nodes.get(item.target_entry_id) !== item.target_revision
      )
        ctx.addIssue({ code: 'custom', message: 'Revision mismatch' });
      if (
        item.kind === 'confirmed' &&
        (!item.is_fact ||
          item.verification !== 'reviewed_evidence' ||
          !item.evidence_refs?.length ||
          !item.evidence_refs.every(
            (ref) => evidenceIdentity.safeParse(ref).success,
          ) ||
          !reviewIdentity.safeParse(item.review_provenance).success ||
          item.declared_target_revision !== item.target_revision)
      )
        ctx.addIssue({ code: 'custom', message: 'Invalid confirmed evidence' });
      if (item.kind !== 'confirmed' && item.is_fact)
        ctx.addIssue({ code: 'custom', message: 'Candidate cannot be fact' });
    }
  });
export type LearningRelations = z.infer<typeof projection>;
export function parseLearningRelations(
  raw: unknown,
  expectedRoot?: string,
): LearningRelations {
  const result = z.object({ data: projection }).safeParse(raw);
  if (
    !result.success ||
    (expectedRoot !== undefined &&
      result.data.data.root_entry_id !== expectedRoot)
  )
    throw new Error('关系响应未通过版本与证据核对。');
  return result.data.data;
}
export async function getLearningRelations(
  entryId: string,
  depth: number,
  kinds: RelationKind[],
  signal?: AbortSignal,
) {
  if (!entryId.trim() || ![1, 2, 3].includes(depth) || !kinds.length)
    throw new Error('请选择正式条目、有限深度和至少一种关系。');
  const query = new URLSearchParams({
    direction: 'both',
    visibility: 'all',
    max_depth: String(depth),
    max_nodes: '40',
  });
  kinds.forEach((kind) => query.append('kind', kind));
  const raw = await apiFetch<unknown>(
    '/api/growth/local/knowledge/relations/' +
      encodeURIComponent(entryId.trim()) +
      '/traverse?' +
      query,
    { signal },
  );
  return parseLearningRelations(raw, entryId.trim());
}
