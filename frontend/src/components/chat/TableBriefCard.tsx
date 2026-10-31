"use client";

/**
 * Карточка паспорта таблицы (TableBrief): summary, сущности и метрики, размер,
 * период, статус описания колонок и замечания о данных.
 * compact — строка под названием таблицы в модалке выбора.
 */
import Icon from "@/components/ui/Icon";
import { useDateLocale, useT } from "@/i18n/useT";
import type { TableBrief } from "@/types/datasource";

export default function TableBriefCard({ brief, compact = false }: { brief: TableBrief; compact?: boolean }) {
  const t = useT();
  const locale = useDateLocale();
  const status =
    brief.description_status === "pending"
      ? t("brief.descriptionPending")
      : brief.description_status === "failed"
        ? t("brief.descriptionFailed")
        : null;
  const meta = [
    t("brief.size", { rows: brief.rows.toLocaleString(locale), columns: brief.columns.toLocaleString(locale) }),
    brief.period_from && brief.period_to ? t("brief.period", { from: brief.period_from, to: brief.period_to }) : null,
  ].filter(Boolean);

  const keyColumns = [
    { label: t("brief.entities"), names: brief.entities ?? [] },
    { label: t("brief.metrics"), names: brief.metrics ?? [] },
  ].filter((row) => row.names.length > 0);

  return (
    <div className={compact ? "space-y-1" : "space-y-2 rounded-xl border border-graphite-800 bg-graphite-950/50 px-3.5 py-3"}>
      {!compact && <div className="font-mono text-[12px] text-graphite-200">{brief.table}</div>}
      {!compact && brief.summary && <p className="text-[13px] text-graphite-300">{brief.summary}</p>}
      {!compact &&
        keyColumns.map((row) => (
          <div key={row.label} className="text-[12px] text-graphite-300">
            <span className="text-graphite-500">{row.label}:</span> {row.names.join(", ")}
          </div>
        ))}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-graphite-400">
        <span>{meta.join(" · ")}</span>
        {status && (
          <span
            className={`rounded-md border px-1.5 py-px ${
              brief.description_status === "failed" ? "border-bad/30 bg-bad/10 text-bad" : "border-warn/30 bg-warn/10 text-warn"
            }`}
          >
            {status}
          </span>
        )}
      </div>
      {brief.warnings.length > 0 && (
        <div className="flex gap-1.5 text-[11px] text-warn">
          <Icon name="alertTriangle" size={12} className="mt-px shrink-0" />
          <div>
            {!compact && <div className="font-medium">{t("brief.warnings")}</div>}
            {brief.warnings.map((warning) => (
              <div key={warning}>{warning}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
