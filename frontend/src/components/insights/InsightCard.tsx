"use client";

/**
 * Карточка вкладки «Инсайты» (F-17): вопрос и вывод, читаемые за пару секунд.
 * Числа выделены, текст обрезан до четырёх строк; график только чипом —
 * сам он рисуется в увеличенной карточке, чтобы вкладка оставалась лёгкой.
 */
import Icon from "@/components/ui/Icon";
import { useT } from "@/i18n/useT";
import { markText, type InsightCardData } from "@/lib/insights";

/** Текст с выделенными числами и совпадениями поиска. */
export function Marked({ text, query = "" }: { text: string; query?: string }) {
  return (
    <>
      {markText(text, query).map((part, i) =>
        part.kind === "match" ? (
          <mark key={i} className="rounded-sm bg-warn/30 px-px text-graphite-50">
            {part.text}
          </mark>
        ) : part.kind === "num" ? (
          <span key={i} className="font-medium tabular-nums text-graphite-50">
            {part.text}
          </span>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  );
}

/** Чип «График» / «Графики · N»; нет графиков — нет чипа. */
export function ChartChip({ count, onClick }: { count: number; onClick?: () => void }) {
  const t = useT();
  if (count === 0) return null;
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick?.();
      }}
      className="inline-flex h-6 shrink-0 items-center gap-1 rounded-md border border-graphite-700 bg-graphite-850 px-1.5 text-[10.5px] font-medium text-graphite-300 transition hover:border-graphite-600 hover:text-graphite-50"
    >
      <Icon name="chart" size={12} />
      <span>{count === 1 ? t("insights.chart") : t("insights.charts", { n: count })}</span>
    </button>
  );
}

interface Props {
  card: InsightCardData;
  query?: string;
  onOpen: () => void;
  onOpenChart: () => void;
}

export default function InsightCard({ card, query = "", onOpen, onOpenChart }: Props) {
  const t = useT();

  return (
    <article
      role="button"
      tabIndex={0}
      data-insight-key={card.key}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      aria-label={t("insights.open")}
      className="flex h-full cursor-pointer flex-col rounded-xl border border-graphite-800 bg-graphite-900 p-3.5 shadow-sm transition hover:border-graphite-600 hover:bg-graphite-850 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-graphite-500"
    >
      <h3 className="line-clamp-2 text-[13px] font-semibold leading-snug text-graphite-50" title={card.question}>
        <Marked text={card.question} query={query} />
      </h3>

      <div className="mt-2 line-clamp-4 flex-1 text-[12.5px] leading-relaxed text-graphite-300">
        {card.headline && (
          <p className="text-graphite-200">
            <Marked text={card.headline} query={query} />
          </p>
        )}
        {card.details.map((detail, i) => (
          <p key={`d${i}`} className="pl-3 -indent-3">
            <span className="text-graphite-500">• </span>
            <Marked text={detail} query={query} />
          </p>
        ))}
        {card.tileInsights.map((item, i) => (
          <p key={`t${i}`} className="pl-3 -indent-3">
            <span className="text-graphite-500">• </span>
            <span className="font-medium text-graphite-200">{item.title}</span>
            <span className="text-graphite-500"> — </span>
            <Marked text={item.text} query={query} />
          </p>
        ))}
      </div>

      <div className="mt-3 flex min-h-6 items-center justify-between gap-2 text-[10.5px] text-graphite-500">
        <span className="truncate">{card.fromTile ? t("insights.fromCanvas") : card.time}</span>
        <ChartChip count={card.charts.length} onClick={onOpenChart} />
      </div>
    </article>
  );
}
