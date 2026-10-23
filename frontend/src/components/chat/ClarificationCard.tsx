"use client";

/**
 * Карточка уточнения (clarification_needed): текст сбоя и кнопки из suggestions.
 * «Пропустить…» отправляет {action: "skip_step"}, остальные варианты открывают
 * поле ввода — ответ уходит свободным текстом в recovery_response.
 */
import { useState } from "react";
import Icon from "@/components/ui/Icon";
import { useT } from "@/i18n/useT";
import type { ClarificationEvent } from "@/types/chat";

interface Props {
  data: ClarificationEvent["data"];
  /** Неактивная карточка (уже ответили или задан новый вопрос) — без кнопок. */
  active: boolean;
  onSkip: () => void;
  onAnswer: (text: string) => void;
}

function isSkip(suggestion: string): boolean {
  return /пропустить|skip/i.test(suggestion);
}

export default function ClarificationCard({ data, active, onSkip, onAnswer }: Props) {
  const t = useT();
  const [mode, setMode] = useState<string | null>(null);
  const [text, setText] = useState("");

  function submit() {
    if (!text.trim()) return;
    onAnswer(text.trim());
    setText("");
    setMode(null);
  }

  return (
    <div className="mt-2 max-w-xl rounded-xl border border-warn/30 bg-warn/10 px-3.5 py-3 text-[13px]">
      <div className="flex items-start gap-2 text-graphite-100">
        <Icon name="refresh" size={14} className="mt-0.5 shrink-0 text-warn" />
        <div className="min-w-0">
          <div className="font-medium">{t("chat.clarifyTitle")}</div>
          <div className="mt-1 whitespace-pre-wrap break-words text-graphite-300">{data.error}</div>
        </div>
      </div>
      {active && (
        <div className="mt-3 flex flex-wrap gap-2">
          {(data.suggestions || []).map((suggestion) => (
            <button
              key={suggestion}
              onClick={() => (isSkip(suggestion) ? onSkip() : setMode(suggestion))}
              className={`rounded-lg border px-2.5 py-1 text-xs transition ${
                mode === suggestion
                  ? "border-graphite-300 bg-graphite-700 text-graphite-50"
                  : "border-graphite-600 bg-graphite-850 text-graphite-200 hover:bg-graphite-800"
              }`}
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
      {active && mode && (
        <div className="mt-2 flex items-center gap-2">
          <input
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={mode}
            className="min-w-0 flex-1 rounded-lg border border-graphite-600 bg-graphite-900 px-2.5 py-1.5 text-xs text-graphite-100 outline-none focus:border-graphite-400"
          />
          <button
            onClick={submit}
            disabled={!text.trim()}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-graphite-100 text-graphite-950 disabled:bg-graphite-700 disabled:text-graphite-500"
            title={t("chat.send")}
          >
            <Icon name="arrowUp" size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
