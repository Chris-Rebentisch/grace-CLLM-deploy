"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type InputBoxProps = {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  history?: string[];
  placeholder?: string;
};

// Keyboard handling per spec §15.5:
//   Enter       → submit
//   Shift+Enter → newline
//   Cmd/Ctrl+K  → focus input
//   ArrowUp/Dn  → navigate sent-message history when caret at edge
//   Esc         → clear current draft
export function InputBox({
  onSubmit,
  disabled,
  history = [],
  placeholder = "Ask GrACE…",
}: InputBoxProps) {
  const [value, setValue] = useState("");
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        ref.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const submit = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setValue("");
    setHistoryIndex(null);
  }, [value, disabled, onSubmit]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === "Escape") {
      setValue("");
      setHistoryIndex(null);
      return;
    }
    const textarea = e.currentTarget;
    const atStart = textarea.selectionStart === 0 && textarea.selectionEnd === 0;
    const atEnd =
      textarea.selectionStart === textarea.value.length &&
      textarea.selectionEnd === textarea.value.length;
    if (e.key === "ArrowUp" && atStart && history.length > 0) {
      e.preventDefault();
      const next = historyIndex === null ? history.length - 1 : Math.max(0, historyIndex - 1);
      setHistoryIndex(next);
      setValue(history[next] ?? "");
      return;
    }
    if (e.key === "ArrowDown" && atEnd && historyIndex !== null) {
      e.preventDefault();
      const next = historyIndex + 1;
      if (next >= history.length) {
        setHistoryIndex(null);
        setValue("");
      } else {
        setHistoryIndex(next);
        setValue(history[next] ?? "");
      }
    }
  }

  return (
    <form
      className="flex items-end gap-2 border-t border-border bg-background px-4 py-3"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Textarea
        ref={ref}
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          setValue(e.target.value);
          setHistoryIndex(null);
        }}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        aria-label="Chat input"
        rows={2}
        className={cn(
          "min-h-[44px] resize-none",
          "focus-visible:ring-1 focus-visible:ring-ring",
        )}
      />
      <Button type="submit" disabled={disabled || value.trim().length === 0}>
        Send
      </Button>
    </form>
  );
}
