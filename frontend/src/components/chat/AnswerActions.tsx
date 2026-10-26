"use client";

/**
 * Действия под завершённым ответом агента (F-12):
 * - «Скопировать ответ» — текст ответа в буфер обмена;
 * - «Спросить об этом» — подставляет цитату ответа в композер, вопрос уйдёт
 *   с reply_to = turn_id этого ответа.
 */
import { useEffect, useRef, useState } from "react";
import Icon from "@/components/ui/Icon";
import { copyText } from "@/lib/clipboard";
import { useT } from "@/i18n/useT";

interface Props {
  /** Готовый текст ответа для буфера обмена. */
  text: string;
  /** Есть turn_id — можно сослаться на ответ. */
  canAsk: boolean;
  onAskAbout: () => void;
}

type CopyState = "idle" | "copied" | "error";

export default function AnswerActions({ text, canAsk, onAskAbout }: Props) {
  const t = useT();
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (resetTimer.current) clearTimeout(resetTimer.current);
    },
    [],
  );

  async function handleCopy() {
    const ok = await copyText(text);
    setCopyState(ok ? "copied" : "error");
    if (resetTimer.current) clearTimeout(resetTimer.current);
    resetTimer.current = setTimeout(() => setCopyState("idle"), 1800);
  }

  const copyLabel =
    copyState === "copied"
      ? t("chat.copied")
      : copyState === "error"
        ? t("chat.copyError")
        : t("chat.copyAnswer");

  return (
    <div className="mt-2 flex items-center gap-1">
      <button
        type="button"
        onClick={handleCopy}
        disabled={!text.trim()}
        title={t("chat.copyAnswer")}
        className={`inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] transition hover:bg-graphite-800 disabled:cursor-not-allowed disabled:opacity-50 ${
          copyState === "copied"
            ? "text-good hover:text-good"
            : copyState === "error"
              ? "text-bad hover:text-bad"
              : "text-graphite-500 hover:text-graphite-200"
        }`}
      >
        <Icon name={copyState === "copied" ? "check" : "copy"} size={12} />
        <span>{copyLabel}</span>
      </button>
      <button
        type="button"
        onClick={onAskAbout}
        disabled={!canAsk}
        title={t("chat.askAbout")}
        className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] text-graphite-500 transition hover:bg-graphite-800 hover:text-graphite-200 disabled:cursor-not-allowed disabled:opacity-40"
      >
        <Icon name="reply" size={12} />
        <span>{t("chat.askAbout")}</span>
      </button>
    </div>
  );
}
