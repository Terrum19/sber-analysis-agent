"use client";

/**
 * Карточка паспорта таблицы из DatasourceOut.profiles (#96): строки, колонки,
 * период, краткое описание, бейдж статуса описания колонок и предупреждения
 * загрузчика. Показывает только те поля, что прислал бэкенд.
 */
import { type ReactNode, useState } from "react";
import type { TableBrief } from "@/types/datasource";
import type { DatabaseFolder } from "@/store/sessionStore";
import { useT } from "@/i18n/useT";
import { useWorkWithTable } from "@/hooks/useStartSession";

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("ru-RU");
}

export interface TablePassportProps {
  brief: TableBrief;
  children?: ReactNode;
  database?: DatabaseFolder;
  sessionId?: string;
  onWorkWithTable?: (table: string) => Promise<void> | void;
  showAction?: boolean;
}

export default function TablePassport({
  brief,
  children,
  database,
  sessionId,
  onWorkWithTable,
  showAction,
}: TablePassportProps) {
  const t = useT();
  const workWithTable = useWorkWithTable();
  const [isBusy, setIsBusy] = useState(false);

  const facts: string[] = [];
  if (typeof brief.rows === "number") facts.push(t("passport.rows", { n: brief.rows.toLocaleString("ru-RU") }));
  if (typeof brief.columns === "number") facts.push(t("passport.columns", { n: brief.columns }));
  if (brief.period_from || brief.period_to) {
    facts.push(
      t("passport.period", {
        from: brief.period_from ? formatDate(brief.period_from) : "…",
        to: brief.period_to ? formatDate(brief.period_to) : "…",
      }),
    );
  }

  async function handleWorkWithTable() {
    if (isBusy) return;
    setIsBusy(true);
    try {
      if (onWorkWithTable) {
        await onWorkWithTable(brief.table);
      } else if (database) {
        await workWithTable(database, brief.table, sessionId);
      }
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-graphite-800 bg-graphite-950/50 px-3 py-2.5 text-xs text-graphite-300 space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-graphite-100">{brief.table}</span>
        {brief.description_status === "pending" && (
          <span className="rounded border border-warn/30 bg-warn/10 px-1.5 py-0.5 text-[10px] text-warn">
            {t("passport.pending")}
          </span>
        )}
        {brief.description_status === "failed" && (
          <span className="rounded border border-bad/30 bg-bad/10 px-1.5 py-0.5 text-[10px] text-bad">
            {t("passport.failed")}
          </span>
        )}
        {facts.length > 0 && <span className="text-graphite-500">{facts.join(" · ")}</span>}
      </div>
      {brief.summary && <p className="leading-relaxed text-graphite-300">{brief.summary}</p>}
      {brief.warnings && brief.warnings.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-warn">
          {brief.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      {(showAction || onWorkWithTable) && (
        <div className="pt-1">
          <button
            type="button"
            onClick={handleWorkWithTable}
            disabled={isBusy}
            className="inline-flex items-center gap-1.5 rounded-md bg-graphite-800 hover:bg-graphite-700 px-2.5 py-1 text-xs font-medium text-graphite-100 border border-graphite-700 transition disabled:opacity-50"
          >
            <span>{isBusy ? "..." : t("passport.useTable")}</span>
          </button>
        </div>
      )}
      {children}
    </div>
  );
}
