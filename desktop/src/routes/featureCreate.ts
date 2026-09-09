/**
 * Shared interpretation of a `chat.send` result triggered from a feature view's
 * inline NLP-create field (Phase 3 Step 3.3). No React, no store — pure, unit-tested.
 *
 * Q1 decision: feature views create entities by sending natural language through
 * `chat.send` (the four-tier agentic path), not a form. The result can be:
 *   - Tier 1 → a `feature` was created  → refresh the list
 *   - Tier 2 → a `disambiguation` popup → resolve via `chat.confirm_action`
 *   - Tier 3/4 → just an assistant `answer` → show it inline
 */
import type { ChatSendResult } from "@ipc/methods";
import type { z } from "zod";

type SendResult = z.infer<typeof ChatSendResult>;
export type ChatFeature = NonNullable<SendResult["feature"]>;
export type ChatDisambiguation = NonNullable<SendResult["disambiguation"]>;
export type ChatConflict = NonNullable<SendResult["conflict"]>;

export type CreateOutcome =
  | { kind: "created"; feature: ChatFeature }
  | { kind: "disambiguation"; pendingActionId: string; options: string[] }
  | { kind: "conflict"; conflict: ChatConflict; answer: string }
  | { kind: "message"; text: string };

export function interpretCreateResult(r: SendResult): CreateOutcome {
  if (r.feature) return { kind: "created", feature: r.feature };
  if (r.disambiguation)
    return {
      kind: "disambiguation",
      pendingActionId: r.disambiguation.pending_action_id,
      options: r.disambiguation.options,
    };
  if (r.conflict) return { kind: "conflict", conflict: r.conflict, answer: r.answer };
  return { kind: "message", text: r.answer };
}

const ACTION_LABELS: Record<string, string> = {
  reminder: "Set a reminder",
  todo: "Add a to-do",
  schedule: "Add to schedule",
  meeting_note: "Capture meeting notes",
  summary_request: "Summarize",
  retrieval_query: "Search my memory",
  conversation: "Just chatting",
  none: "Never mind",
};

export function actionLabel(option: string): string {
  return ACTION_LABELS[option] ?? option;
}
