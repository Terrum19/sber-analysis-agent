"use client";

import Icon from "@/components/ui/Icon";
import { useT } from "@/i18n/useT";
import type { LlmStatus } from "@/types/llm";

/** Индикатор над полем ввода: модель готовится сама, пользователю делать ничего не нужно.
 *  Исключение — missing: модели нет в реестре, сама она не появится. */
export default function LlmStatusBanner({ status }: { status: LlmStatus | null }) {
  const t = useT();
  if (!status || status.state === "ok") return null;

  const missing = status.state === "missing";
  const percent = status.progress != null ? Math.round(status.progress * 100) : null;
  const text =
    status.state === "pulling"
      ? status.detail
        ? t("llm.pullRetry", { detail: status.detail })
        : t("llm.pulling", { percent: percent ?? 0 })
      : missing
      ? t("llm.missing", { model: status.model })
      : status.state === "preparing"
      ? t("llm.preparing")
      : t("llm.connecting");

  return (
    <div className="mb-3 rounded-xl border border-graphite-600 bg-graphite-800 px-3.5 py-2.5 text-xs text-graphite-200">
      <div className="flex items-center gap-2">
        {missing ? (
          <Icon name="x" size={14} className="shrink-0 text-graphite-400" />
        ) : (
          <Icon name="refresh" size={14} className="shrink-0 animate-spin text-graphite-400" />
        )}
        <span>{text}</span>
      </div>
      {status.state === "pulling" && percent != null && (
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-graphite-700">
          <div className="h-full bg-graphite-300 transition-all" style={{ width: `${percent}%` }} />
        </div>
      )}
    </div>
  );
}
