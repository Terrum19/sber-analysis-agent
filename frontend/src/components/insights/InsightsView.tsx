"use client";

/**
 * Вкладка «Инсайты» канваса (F-17): сетка карточек «вопрос → вывод» для
 * быстрого просмотра, поиск, увеличенная карточка и экспорт видимого списка
 * в Markdown или в PDF через системную печать.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import InsightCard from "@/components/insights/InsightCard";
import InsightDialog from "@/components/insights/InsightDialog";
import Icon from "@/components/ui/Icon";
import { useElementSize } from "@/hooks/useElementSize";
import { useT } from "@/i18n/useT";
import { downloadBlob, safeFilename } from "@/lib/download";
import { filterInsights, insightsMarkdown, type InsightCardData } from "@/lib/insights";
import { useSessionStore } from "@/store/sessionStore";

interface Props {
  sessionId: string;
  cards: InsightCardData[];
}

/** Колонки по ширине панели, а не окна: канвас бывает узким и на широком экране. */
function columnsFor(width: number): number {
  if (width >= 1024) return 3;
  if (width >= 640) return 2;
  return 1;
}

export default function InsightsView({ sessionId, cards }: Props) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<{ key: string; charts: boolean } | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [printing, setPrinting] = useState(false);
  const { ref: gridRef, width } = useElementSize<HTMLDivElement>();

  const sessionTitle = useSessionStore(
    (s) => s.databases.flatMap((db) => db.sessions).find((item) => item.id === sessionId)?.title,
  );
  const docTitle = sessionTitle ? t("insights.docTitle", { name: sessionTitle }) : t("insights.docTitleDefault");

  const visible = useMemo(() => filterInsights(cards, query), [cards, query]);
  const filtered = query.trim() !== "";
  // Окно держится за ключ: новая карточка в ленте не сдвигает открытую
  const openIndex = open ? visible.findIndex((card) => card.key === open.key) : -1;

  const close = useCallback(() => {
    const key = open?.key;
    setOpen(null);
    if (key) document.querySelector<HTMLElement>(`[data-insight-key="${CSS.escape(key)}"]`)?.focus();
  }, [open]);

  const goTo = useCallback(
    (index: number) => {
      const card = visible[index];
      if (card) setOpen({ key: card.key, charts: false });
    },
    [visible],
  );

  // Печать: в документ на время печати встаёт чистый список, остальное скрывает @media print
  useEffect(() => {
    if (!printing) return;
    const done = () => setPrinting(false);
    window.addEventListener("afterprint", done, { once: true });
    const frame = requestAnimationFrame(() => window.print());
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("afterprint", done);
    };
  }, [printing]);

  function exportMarkdown() {
    setExportOpen(false);
    const text = insightsMarkdown(visible, { title: docTitle, chart: t("insights.chart") });
    downloadBlob(new Blob([text], { type: "text/markdown;charset=utf-8" }), safeFilename(docTitle, "md"));
  }

  if (cards.length === 0) {
    return (
      <div className="flex h-48 flex-col items-center justify-center space-y-2 rounded-xl border border-graphite-800 bg-graphite-950/40 text-center text-graphite-500">
        <Icon name="sparkle" size={20} className="text-graphite-500" />
        <p className="text-xs">{t("canvas.noInsights")}</p>
      </div>
    );
  }

  const menuItem =
    "flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[12px] text-graphite-200 transition hover:bg-graphite-700 disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <div className="space-y-3">
      {/* Поиск, счётчик и экспорт */}
      <div className="flex items-center gap-2">
        <label className="flex h-8 min-w-0 flex-1 items-center gap-2 rounded-lg border border-graphite-800 bg-graphite-950/60 px-2.5 text-graphite-400 focus-within:border-graphite-600">
          <Icon name="search" size={14} className="shrink-0" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setQuery("");
            }}
            placeholder={t("insights.search")}
            className="min-w-0 flex-1 bg-transparent text-[12.5px] text-graphite-100 placeholder:text-graphite-500 focus:outline-none"
          />
          {filtered && (
            <button
              type="button"
              onClick={() => setQuery("")}
              className="shrink-0 text-graphite-500 hover:text-graphite-200"
              title={t("insights.clearSearch")}
              aria-label={t("insights.clearSearch")}
            >
              <Icon name="x" size={13} />
            </button>
          )}
        </label>
        <span className="shrink-0 text-[11px] tabular-nums text-graphite-500">
          {filtered
            ? t("insights.countFiltered", { shown: visible.length, n: cards.length })
            : t("insights.count", { n: cards.length })}
        </span>
        <div className="relative shrink-0">
          <button
            type="button"
            onClick={() => setExportOpen((v) => !v)}
            disabled={visible.length === 0}
            className="flex h-8 items-center gap-1 rounded-lg border border-graphite-700 bg-graphite-850 px-2.5 text-[12px] text-graphite-300 transition hover:bg-graphite-800 hover:text-graphite-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Icon name="download" size={13} />
            <span>{filtered ? t("insights.exportFiltered", { n: visible.length }) : t("insights.export")}</span>
          </button>
          {exportOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setExportOpen(false)} />
              <div className="absolute right-0 top-full z-50 mt-1 w-40 rounded-lg border border-graphite-700 bg-graphite-850 p-1 shadow-2xl">
                <button type="button" onClick={exportMarkdown} className={menuItem}>
                  {t("insights.exportMd")}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setExportOpen(false);
                    setPrinting(true);
                  }}
                  className={menuItem}
                >
                  {t("insights.exportPrint")}
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {visible.length === 0 ? (
        <p className="py-8 text-center text-xs text-graphite-500">{t("insights.notFound", { q: query.trim() })}</p>
      ) : (
        <div
          ref={gridRef}
          className="grid gap-3"
          style={{ gridTemplateColumns: `repeat(${columnsFor(width)}, minmax(0, 1fr))` }}
        >
          {visible.map((card) => (
            <InsightCard
              key={card.key}
              card={card}
              query={query}
              onOpen={() => setOpen({ key: card.key, charts: false })}
              onOpenChart={() => setOpen({ key: card.key, charts: true })}
            />
          ))}
        </div>
      )}

      {open && openIndex >= 0 && (
        <InsightDialog
          cards={visible}
          index={openIndex}
          sessionId={sessionId}
          query={query}
          focusCharts={open.charts}
          onIndex={goTo}
          onClose={close}
        />
      )}

      {printing &&
        createPortal(
          <div className="insights-print">
            <h1>{docTitle}</h1>
            {visible.map((card) => (
              <section key={card.key}>
                <h2>{card.question}</h2>
                {card.time && <p className="insights-print-meta">{card.time}</p>}
                {card.headline && <p>{card.headline}</p>}
                {(card.details.length > 0 || card.tileInsights.length > 0) && (
                  <ul>
                    {card.details.map((detail, i) => (
                      <li key={`d${i}`}>{detail}</li>
                    ))}
                    {card.tileInsights.map((item, i) => (
                      <li key={`t${i}`}>
                        <strong>{item.title}</strong> — {item.text}
                      </li>
                    ))}
                  </ul>
                )}
                {card.charts.length > 0 && (
                  <p className="insights-print-meta">
                    {t("insights.chart")}: {card.charts.map((chart) => chart.title).join(", ")}
                  </p>
                )}
              </section>
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}
