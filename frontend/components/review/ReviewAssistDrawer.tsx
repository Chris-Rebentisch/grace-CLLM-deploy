"use client";
import { useState } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { InputBox } from "@/components/chat/InputBox";
import { useAssist, useDecide } from "@/lib/query/review";
import type {
  ReviewElement,
  ReviewAssistResponse,
  ReviewAssistAction,
  ReviewAssistTurn,
} from "@/lib/api/types";

// D522 session — Option C: the inline conversational helper for one type.
// A non-technical reviewer can ask questions in plain English; the assistant
// explains and, when they want a change, proposes ONE concrete action mapped
// onto the decision verbs. The reviewer confirms here; nothing is written until
// they click the confirm button.

export type ReviewAssistDrawerProps = {
  sessionId: string;
  element: ReviewElement;
  /** Backend ReviewElementType value ("entity_type" | "relationship"). */
  elementTypeForApi: string;
  friendlyLabel: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

/** Map a plain assistant action to the /decide payload. Returns null for none. */
function actionToDecision(
  action: ReviewAssistAction,
): Record<string, unknown> | null {
  switch (action.action) {
    case "keep":
      return { decision: "approved" };
    case "skip":
      return { decision: "rejected" };
    case "rename":
      return action.new_name
        ? { decision: "renamed", modified_data: { name: action.new_name } }
        : null;
    case "merge":
      return action.merge_with
        ? { decision: "merged", merged_with: action.merge_with }
        : null;
    default:
      return null;
  }
}

export function ReviewAssistDrawer({
  sessionId,
  element,
  elementTypeForApi,
  friendlyLabel,
  open,
  onOpenChange,
}: ReviewAssistDrawerProps) {
  const assist = useAssist(sessionId);
  const decide = useDecide(sessionId);
  const [turns, setTurns] = useState<ReviewAssistTurn[]>([]);
  const [suggested, setSuggested] = useState<ReviewAssistAction | null>(null);

  const name = element.element_name ?? element.name ?? "";

  const send = (text: string) => {
    const history = [...turns];
    setTurns([...history, { role: "user", content: text }]);
    setSuggested(null);
    assist.mutate(
      {
        element_type: elementTypeForApi,
        element_name: name,
        message: text,
        history,
      },
      {
        onSuccess: (raw) => {
          const res = raw as unknown as ReviewAssistResponse;
          setTurns((prev) => [
            ...prev,
            { role: "assistant", content: res.reply },
          ]);
          const action = res.suggested_action;
          setSuggested(action && action.action !== "none" ? action : null);
        },
        onError: () => {
          setTurns((prev) => [
            ...prev,
            {
              role: "assistant",
              content:
                "Sorry — I couldn't reach the assistant just now. You can still use the Yes / Skip buttons.",
            },
          ]);
        },
      },
    );
  };

  const confirmAction = () => {
    if (!suggested) return;
    const payload = actionToDecision(suggested);
    if (!payload) return;
    decide.mutate(
      { element_type: elementTypeForApi, element_name: name, ...payload },
      { onSuccess: () => onOpenChange(false) },
    );
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" data-testid={`assist-drawer-${name}`} className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{friendlyLabel}</SheetTitle>
          <SheetDescription>
            Not sure about this one? Ask me anything — in plain English. I can explain
            it, rename it to what you call it, combine it with another item, or drop it.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-auto pr-1" data-testid={`assist-thread-${name}`}>
          {turns.length === 0 ? (
            <div className="mt-2 text-sm text-slate-400">
              Try: “What is this, in plain terms?” · “I call these something else” ·
              “Isn’t this the same as another item?” · “What happens if I skip it?”
            </div>
          ) : (
            <ul className="flex flex-col gap-3">
              {turns.map((t, i) => (
                <li
                  key={i}
                  data-role={t.role}
                  className={
                    t.role === "user"
                      ? "self-end rounded-lg bg-slate-800 px-3 py-2 text-sm text-white"
                      : "self-start rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-800"
                  }
                >
                  {t.content}
                </li>
              ))}
              {assist.isPending ? (
                <li className="self-start text-sm text-slate-400">Thinking…</li>
              ) : null}
            </ul>
          )}
        </div>

        {suggested ? (
          <div
            data-testid={`assist-suggestion-${name}`}
            className="mt-2 rounded-md border border-emerald-200 bg-emerald-50 p-3"
          >
            {suggested.rationale ? (
              <p className="mb-2 text-sm text-slate-700">{suggested.rationale}</p>
            ) : null}
            <button
              type="button"
              data-testid={`assist-confirm-${name}`}
              onClick={confirmAction}
              disabled={decide.isPending}
              className="rounded-md bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {suggested.button_label || "Confirm"}
            </button>
          </div>
        ) : null}

        <div className="mt-3">
          <InputBox
            onSubmit={send}
            disabled={assist.isPending}
            placeholder="Ask about this item…"
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}
