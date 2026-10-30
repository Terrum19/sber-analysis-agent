"use client";

/**
 * Увеличенная карточка инсайта (F-17): вопрос и вывод целиком, графики хода,
 * экспорт ответа, переход к ходу в чате. ← / → листают видимые карточки, Esc закрывает.
 */
import { useEffect, useRef, useState } from "react";
import DashboardTile from "@/components/canvas/DashboardTile";
import AnswerExport from "@/components/chat/AnswerExport";
import { Marked } from "@/components/insights/InsightCard";
import Icon from "@/components/ui/Icon";
import { useT } from "@/i18n/useT";
import { copyText } from "@/lib/clipboard";
import { insightLines, type InsightCardData } from "@/lib/insights";
import { isKpiTile } from "@/lib/tileLayout";
import { useUiStore } from "@/store/uiStore";
import type { DashboardTile as DashboardTileType } from "@/types/dashboard";

interface Props {
  cards: InsightCardData[];
  index: number;
  sessionId: string;
  query?: string;
  /** Открыто по чипу графика: сразу прокрутить к графикам. */
  focusCharts?: boolean;
  onIndex: (index: number) => void;
  onClose: () => void;
}

/** Спека со снимком данных плитки — для PDF/Excel, как на вкладке «Графики». */
function exportSpec(tile: DashboardTileType): Record<string, unknown> {
  return Array.isArray(tile.snapshot_data) && !(tile.spec?.data as any)?.values
    ? { ...tile.spec, data: { values: tile.snapshot_data } }
    : tile.spec;
}

export default function InsightDialog({ cards, index, sessionId, query = "", focusCharts, onIndex, onClose }: Props) {
  const t = useT();
  const requestFocusTurn = useUiStore((s) => s.requestFocusTurn);
  const dialogRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const chartsRef = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState<"ok" | "error" | null>(null);
  const card = cards[index];

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  // Новая карточка — с начала, а по чипу графика — сразу на графиках
  useEffect(() => {
    setCopied(null);
    if (focusCharts && chartsRef.current) chartsRef.current.scrollIntoView({ block: "start" });
    else bodyRef.current?.scrollTo({ top: 0 });
  }, [index, focusCharts]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
      if (e.key === "ArrowLeft" && index > 0) onIndex(index - 1);
      if (e.key === "ArrowRight" && index < cards.length - 1) onIndex(index + 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, cards.length, onIndex, onClose]);

  if (!card) return null;

  const single = card.charts.length === 1 ? card.charts[0] : undefined;
  const kpis = card.charts.filter(isKpiTile);
  const charts = card.charts.filter((tile) => !isKpiTile(tile));

  async function handleCopy() {
    const ok = await copyText([card.question, "", ...insightLines(card)].join("\n"));
    setCopied(ok ? "ok" : "error");
    setTimeout(() => setCopied(null), 1500);
  }

  const navButton =
    "flex h-7 w-7 items-center justify-center rounded-md text-graphite-400 transition hover:bg-graphite-800 hover:text-graphite-100 disabled:cursor-not-allowed disabled:opacity-40";
  const actionButton =
    "inline-flex items-center gap-1 rounded-md border border-graphite-700 bg-graphite-850 px-2 py-1 text-[11px] text-graphite-300 transition hover:bg-graphite-800 hover:text-graphite-50";

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/60 backdrop-blur-sm sm:items-center sm:p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={card.question}
        tabIndex={-1}
        className="flex w-full max-w-[720px] flex-col overflow-hidden border-graphite-700 bg-graphite-900 text-graphite-100 shadow-2xl outline-none sm:max-h-[90vh] sm:rounded-xl sm:border"
      >
        {/* Шапка: позиция в списке, листание, закрыть */}
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-graphite-800 px-4 py-2">
          <span className="text-[11px] text-graphite-500">
            {t("insights.position", { i: index + 1, n: cards.length })}
            {card.time && <span> · {card.time}</span>}
          </span>
          <div className="flex items-center gap-0.5">
            <button
              type="button"
              className={navButton}
              onClick={() => onIndex(index - 1)}
              disabled={index === 0}
              title={t("insights.prev")}
              aria-label={t("insights.prev")}
            >
              <Icon name="chevronRight" className="rotate-180" />
            </button>
            <button
              type="button"
              className={navButton}
              onClick={() => onIndex(index + 1)}
              disabled={index >= cards.length - 1}
              title={t("insights.next")}
              aria-label={t("insights.next")}
            >
              <Icon name="chevronRight" />
            </button>
            <span className="mx-1 h-4 w-px bg-graphite-800" />
            <button type="button" className={navButton} onClick={onClose} title={t("common.close")} aria-label={t("common.close")}>
              <Icon name="x" />
            </button>
          </div>
        </div>

        <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <p className="text-[10.5px] font-medium uppercase tracking-wide text-graphite-500">
            {card.fromTile ? t("insights.fromCanvas") : t("insights.question")}
          </p>
          <h2 className="mt-1 text-[15px] font-semibold leading-snug text-graphite-50">
            <Marked text={card.question} query={query} />
          </h2>

          {card.headline && (
            <p className="mt-3 text-[13.5px] leading-relaxed text-graphite-100">
              <Marked text={card.headline} query={query} />
            </p>
          )}
          {card.details.length > 0 && (
            <ul className="mt-2 space-y-1 text-[13px] leading-relaxed text-graphite-300">
              {card.details.map((detail, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-graphite-500">•</span>
                  <span>
                    <Marked text={detail} query={query} />
                  </span>
                </li>
              ))}
            </ul>
          )}

          {card.tileInsights.length > 0 && (
            <>
              <p className="mt-4 text-[10.5px] font-medium uppercase tracking-wide text-graphite-500">
                {t("insights.dashboardTiles")}
              </p>
              <ul className="mt-1.5 space-y-1.5 text-[13px] leading-relaxed text-graphite-300">
                {card.tileInsights.map((item, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="text-graphite-500">•</span>
                    <span>
                      <span className="font-medium text-graphite-100">{item.title}</span>
                      <span className="text-graphite-500"> — </span>
                      <Marked text={item.text} query={query} />
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}

          {card.charts.length > 0 && (
            <div ref={chartsRef} className="mt-5 scroll-mt-4">
              <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wide text-graphite-500">
                {t("insights.chartsSection")}
              </p>
              <div className="grid grid-cols-2 gap-3">
                {kpis.map((tile, i) => (
                  <div key={`k${tile.id ?? i}`} className="h-32">
                    <DashboardTile tile={tile} preview />
                  </div>
                ))}
                {charts.map((tile, i) => (
                  <div key={`c${tile.id ?? i}`} className="col-span-2 h-72">
                    <DashboardTile tile={tile} />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Действия: экспорт ответа, переход в чат, копирование */}
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-t border-graphite-800 px-4 py-2.5">
          <AnswerExport
            sessionId={sessionId}
            title={card.question}
            question={card.fromTile ? undefined : card.question}
            headline={card.headline}
            details={[...card.details, ...card.tileInsights.map((item) => `${item.title} — ${item.text}`)]}
            spec={single ? exportSpec(single) : undefined}
            resultTable={card.resultTable ?? single?.result_table}
            inline
          />
          <span className="flex-1" />
          {card.turnId && (
            <button
              type="button"
              className={actionButton}
              onClick={() => {
                requestFocusTurn(card.turnId!);
                onClose();
              }}
            >
              <Icon name="chat" size={12} />
              <span>{t("insights.toChat")}</span>
            </button>
          )}
          <button type="button" className={actionButton} onClick={handleCopy}>
            <Icon name={copied === "ok" ? "check" : "copy"} size={12} />
            <span>
              {copied === "ok" ? t("insights.copied") : copied === "error" ? t("insights.copyError") : t("insights.copy")}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
