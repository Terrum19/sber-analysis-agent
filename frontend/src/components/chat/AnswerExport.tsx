"use client";

/**
 * Кнопки экспорта ответа агента и отдельной плитки (F-10).
 *
 * Excel требует `result_ref` (temp_result_vN), PDF собирается из того, что
 * пользователь видит: вопрос, заголовок инсайта, детали и спека графика.
 * Имя файла приходит из Content-Disposition (E-01/E-03), иначе — запасное.
 */
import { useState } from "react";
import { exportArtifact } from "@/api/client";
import { downloadBlob, safeFilename } from "@/lib/download";
import { useT } from "@/i18n/useT";
import Icon from "@/components/ui/Icon";

interface Props {
  sessionId?: string;
  question?: string;
  headline?: string;
  details?: string[];
  spec?: Record<string, unknown>;
  resultTable?: string;
  /** Компактный вид для узкой панели. */
  compact?: boolean;
  /** Встроить в шапку плитки: без внешнего отступа и служебных подписей. */
  inline?: boolean;
}

export default function AnswerExport({
  sessionId,
  question,
  headline,
  details,
  spec,
  resultTable,
  compact = false,
  inline = false,
}: Props) {
  const t = useT();
  const [busy, setBusy] = useState<"xlsx" | "pdf" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canExcel = Boolean(sessionId && resultTable);
  const canPdf = Boolean(sessionId && (resultTable || headline || details?.length || spec));
  if (!sessionId) return null;

  async function handleExport(format: "xlsx" | "pdf") {
    if (!sessionId) return;
    setBusy(format);
    setError(null);
    try {
      const fallback = safeFilename(headline || t("chat.exportFallbackName"), format);
      const { blob, filename } = await exportArtifact(
        {
          session_id: sessionId,
          format,
          result_ref: resultTable ?? null,
          question,
          headline,
          details,
          spec,
        },
        fallback,
      );
      downloadBlob(blob, filename);
    } catch (err: any) {
      if (err?.status === 501) {
        setError(t("chat.exportPdfInProgress"));
      } else {
        setError(err?.detail || err?.message || t("chat.exportError"));
      }
    } finally {
      setBusy(null);
    }
  }

  const buttonClass = inline
    ? "inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[10px] font-medium text-graphite-400 transition hover:bg-graphite-800 hover:text-graphite-100 disabled:cursor-not-allowed disabled:opacity-50"
    : `inline-flex items-center gap-1 rounded-md border border-graphite-700 bg-graphite-850 text-graphite-300 transition hover:bg-graphite-800 hover:text-graphite-50 disabled:cursor-not-allowed disabled:opacity-50 ${
        compact ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-[11px]"
      }`;

  return (
    <div
      className={inline ? "flex items-center gap-1" : "mt-2 flex flex-wrap items-center gap-1.5"}
      title={inline && error ? error : undefined}
    >
      <button
        type="button"
        onClick={() => handleExport("xlsx")}
        disabled={!canExcel || busy !== null}
        className={buttonClass}
        title={canExcel ? t("chat.exportExcel") : t("chat.exportNoTable")}
      >
        <Icon
          name={busy === "xlsx" ? "refresh" : "download"}
          size={compact ? 11 : 12}
          className={busy === "xlsx" ? "animate-spin" : ""}
        />
        <span>{t("chat.exportExcel")}</span>
      </button>
      <button
        type="button"
        disabled
        className={buttonClass}
        title={t("chat.exportPdfSoon")}
      >
        <Icon
          name="download"
          size={compact ? 11 : 12}
        />
        <span>{t("chat.exportPdf")}</span>
      </button>
      {busy && !inline && <span className="text-[11px] text-graphite-500">{t("chat.exporting")}</span>}
      {error && !inline && <span className="text-[11px] leading-snug text-bad">{error}</span>}
    </div>
  );
}
