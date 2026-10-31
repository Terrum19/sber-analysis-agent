"use client";

/**
 * Раскрытый шаг плана (#239): что агент сделал с данными и что получилось.
 * Операция (purpose, тул, аргументы, код), вход (исходная таблица или
 * результат прошлого шага — ссылкой) и результат: превью из события, догрузка
 * по GET /sessions/{id}/results/{table} и выгрузка в .xlsx. Для упавшего шага —
 * ошибка и попытки исправления. Старые сообщения без деталей показывают что есть.
 */
import { useState, type ReactNode } from "react";
import { exportArtifact, getStepResult, isNotFound } from "@/api/client";
import Icon from "@/components/ui/Icon";
import { dictionaries, type MessageKey } from "@/i18n/dictionary";
import { useDateLocale, useT, type Translate } from "@/i18n/useT";
import { copyText } from "@/lib/clipboard";
import { downloadBlob, safeFilename } from "@/lib/download";
import { useUiStore, type Locale } from "@/store/uiStore";
import type { ResultColumn, StepAttempt, StepState } from "@/types/chat";

/** Сколько строк догружать по «Показать ещё». */
const PAGE_ROWS = 50;
/** Ключи аргументов, значения которых — колонки: показываем чипами. */
const COLUMN_KEY = /(^|_)(col|cols|column|columns|by|group|groups|field|fields|x|y|target|metric|metrics|value|values|date|time|on|keys?)($|_)/i;
const NUMERIC_TYPE = /INT|DOUBLE|FLOAT|DECIMAL|REAL|NUMERIC|HUGEINT/i;

const SQL_KEYWORDS = new Set(
  (
    "select from where group by order having limit offset as and or not in is null join left right inner outer full cross on using " +
    "with case when then else end distinct union all over partition rows between preceding following current row asc desc " +
    "cast like ilike exists true false interval filter qualify create table insert into values"
  ).split(" "),
);
const PYTHON_KEYWORDS = new Set(
  (
    "import from as def return if elif else for while in not and or is None True False with try except finally raise " +
    "lambda class pass break continue yield global nonlocal assert del print"
  ).split(" "),
);
const TOKEN = /(--[^\n]*|#[^\n]*|'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][A-Za-z0-9_]*\b)/g;

function plural(locale: Locale, n: number, ru: [string, string, string], en: [string, string]): string {
  const value = n.toLocaleString(locale === "en" ? "en-US" : "ru-RU");
  if (locale === "en") return `${value} ${n === 1 ? en[0] : en[1]}`;
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${value} ${ru[0]}`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${value} ${ru[1]}`;
  return `${value} ${ru[2]}`;
}

/** Человеческое название тула; неизвестный тул — как есть. */
export function toolLabel(t: Translate, tool: string | undefined): string | undefined {
  if (!tool) return undefined;
  const key = `tool.${tool}`;
  return key in dictionaries.ru ? t(key as MessageKey) : tool;
}

function formatCell(value: unknown, locale: string): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString(locale, { maximumFractionDigits: 4 });
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Подсветка SQL/Python без библиотек: комментарии, строки, числа, ключевые слова. */
function highlight(code: string, language: "sql" | "python"): ReactNode[] {
  const keywords = language === "sql" ? SQL_KEYWORDS : PYTHON_KEYWORDS;
  const comment = language === "sql" ? "--" : "#";
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of code.matchAll(TOKEN)) {
    const token = match[0];
    const at = match.index ?? 0;
    let className: string | null = null;
    if (token.startsWith(comment)) className = "text-graphite-500 italic";
    else if (token.startsWith("--") || token.startsWith("#")) className = null;
    else if (token[0] === "'" || token[0] === '"') className = "text-good";
    else if (/^\d/.test(token)) className = "text-warn";
    else if (keywords.has(language === "sql" ? token.toLowerCase() : token)) className = "font-semibold text-graphite-50";
    if (!className) continue;
    if (at > last) out.push(code.slice(last, at));
    out.push(
      <span key={at} className={className}>
        {token}
      </span>,
    );
    last = at + token.length;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="text-[10.5px] font-medium uppercase tracking-wide text-graphite-500">{title}</div>
      {children}
    </div>
  );
}

function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="inline-block max-w-full truncate rounded border border-graphite-700 bg-graphite-850 px-1.5 py-px font-mono text-[11px] text-graphite-200">
      {children}
    </span>
  );
}

function ArgumentValue({ name, value }: { name: string; value: unknown }) {
  const columns = COLUMN_KEY.test(name);
  if (Array.isArray(value) && value.every((item) => typeof item === "string" || typeof item === "number")) {
    if (value.length === 0) return <span className="text-graphite-500">—</span>;
    return (
      <span className="flex flex-wrap gap-1">
        {value.map((item, index) => (
          <Chip key={index}>{String(item)}</Chip>
        ))}
      </span>
    );
  }
  if (typeof value === "string" && columns) return <Chip>{value}</Chip>;
  if (value !== null && typeof value === "object") {
    return <code className="whitespace-pre-wrap break-all font-mono text-[11px] text-graphite-300">{JSON.stringify(value)}</code>;
  }
  return <span className="break-words text-graphite-200">{value === null || value === undefined ? "—" : String(value)}</span>;
}

function Arguments({ args }: { args?: Record<string, unknown> }) {
  const t = useT();
  const entries = Object.entries(args ?? {}).filter(([, value]) => value !== undefined);
  if (!args) return null;
  if (entries.length === 0) return <div className="text-graphite-500">{t("step.noArguments")}</div>;
  return (
    <dl className="grid grid-cols-[minmax(0,max-content)_1fr] gap-x-3 gap-y-1">
      {entries.map(([name, value]) => (
        <div key={name} className="contents">
          <dt className="truncate font-mono text-[11px] text-graphite-500">{name}</dt>
          <dd className="min-w-0">
            <ArgumentValue name={name} value={value} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function CodeBlock({ code, language }: { code: string; language: "sql" | "python" }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative rounded-md border border-graphite-800 bg-graphite-900">
      <div className="flex items-center justify-between border-b border-graphite-800 px-2 py-1 text-[10.5px] text-graphite-500">
        <span className="font-mono uppercase">{language}</span>
        <button
          type="button"
          onClick={async () => {
            if (await copyText(code)) {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }
          }}
          className="inline-flex items-center gap-1 rounded px-1 hover:bg-graphite-800 hover:text-graphite-200"
        >
          <Icon name={copied ? "check" : "copy"} size={11} />
          {copied ? t("step.copied") : t("step.copy")}
        </button>
      </div>
      <pre className="max-h-64 overflow-auto px-2.5 py-2 font-mono text-[11px] leading-relaxed text-graphite-200">
        <code>{highlight(code, language)}</code>
      </pre>
    </div>
  );
}

/** Откуда шаг берёт данные: после выполнения — по input_table, до — по ссылке из плана. */
function InputLine({
  step,
  tableName,
  stepNumber,
  onOpenStep,
}: {
  step: StepState;
  tableName: (table: string) => string;
  stepNumber: (stepId: number) => number | undefined;
  onOpenStep: (stepId: number) => void;
}) {
  const t = useT();
  const ref = step.input;
  const fromRef = ref ? /^\$?step_(\d+)$/.exec(ref) : null;
  const sourceStep = step.input_step_id ?? (fromRef ? Number(fromRef[1]) : undefined);
  if (sourceStep !== undefined) {
    const number = stepNumber(sourceStep);
    if (number === undefined) return <span className="text-graphite-300">{t("step.inputPrev")}</span>;
    return (
      <button
        type="button"
        onClick={() => onOpenStep(sourceStep)}
        className="inline-flex items-center gap-1 text-graphite-200 underline decoration-graphite-600 underline-offset-2 hover:decoration-graphite-300"
      >
        {t("step.inputStep", { n: number })}
      </button>
    );
  }
  const table = step.input_table ?? (ref && !ref.startsWith("$") ? ref : undefined);
  if (table && !/^temp_result_v\d+$/.test(table)) {
    return (
      <span className="text-graphite-300">
        {t("step.inputSource")} <Chip>{tableName(table)}</Chip>
      </span>
    );
  }
  if (table) return <span className="text-graphite-300">{t("step.inputTemp")} <Chip>{table}</Chip></span>;
  if (ref === "$prev") return <span className="text-graphite-300">{t("step.inputPrev")}</span>;
  if (ref === "$source") return <span className="text-graphite-300">{t("step.inputSource")}</span>;
  return null;
}

function ResultTable({ columns, rows }: { columns: ResultColumn[]; rows: Record<string, unknown>[] }) {
  const locale = useDateLocale();
  return (
    <div className="max-h-72 overflow-auto rounded-md border border-graphite-800">
      <table className="min-w-full border-collapse text-[11.5px]">
        <thead className="sticky top-0 bg-graphite-850">
          <tr>
            {columns.map((column) => (
              <th
                key={column.name}
                className="whitespace-nowrap border-b border-graphite-800 px-2 py-1 text-left font-medium text-graphite-300"
                title={column.type}
              >
                {column.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="odd:bg-graphite-900/40">
              {columns.map((column) => (
                <td
                  key={column.name}
                  className={`whitespace-nowrap border-b border-graphite-800/60 px-2 py-0.5 text-graphite-200 ${
                    NUMERIC_TYPE.test(column.type) ? "text-right tabular-nums" : ""
                  }`}
                >
                  {formatCell(row[column.name], locale)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Result({ step, sessionId }: { step: StepState; sessionId?: string }) {
  const t = useT();
  const locale = useUiStore((s) => s.locale);
  const [loaded, setLoaded] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [gone, setGone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const preview = step.preview ?? [];
  const rows = [...preview, ...loaded];
  const columns: ResultColumn[] =
    step.result_columns ?? Object.keys(preview[0] ?? {}).map((name) => ({ name, type: "" }));
  const total = step.rows;
  const table = step.result_table;
  const canLoad = Boolean(sessionId && table && !gone && total !== undefined && rows.length < total);

  async function loadMore() {
    if (!sessionId || !table) return;
    setLoading(true);
    setError(null);
    try {
      const page = await getStepResult(sessionId, table, rows.length, PAGE_ROWS);
      setLoaded((prev) => [...prev, ...page.rows]);
    } catch (err) {
      if (isNotFound(err)) setGone(true);
      else setError(t("step.loadError"));
    } finally {
      setLoading(false);
    }
  }

  async function download() {
    if (!sessionId || !table) return;
    setExporting(true);
    setError(null);
    try {
      const fallback = safeFilename(step.purpose || `step_${step.step_id}`, "xlsx");
      const { blob, filename } = await exportArtifact({ session_id: sessionId, format: "xlsx", result_ref: table }, fallback);
      downloadBlob(blob, filename);
    } catch (err) {
      if (isNotFound(err)) setGone(true);
      else setError(t("chat.exportError"));
    } finally {
      setExporting(false);
    }
  }

  if (!table && total === undefined && preview.length === 0) return null;
  const buttonClass =
    "inline-flex items-center gap-1 rounded-md border border-graphite-700 bg-graphite-850 px-2 py-0.5 text-[11px] text-graphite-300 transition hover:bg-graphite-800 hover:text-graphite-50 disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <Section title={t("step.result")}>
      {total !== undefined && (
        <div className="text-graphite-300">
          {plural(locale, total, ["строка", "строки", "строк"], ["row", "rows"])} ×{" "}
          {plural(locale, columns.length, ["колонка", "колонки", "колонок"], ["column", "columns"])}
          {table && <span className="ml-2 font-mono text-[10.5px] text-graphite-500">{table}</span>}
        </div>
      )}
      {columns.length > 0 && rows.length > 0 && <ResultTable columns={columns} rows={rows} />}
      {gone && <div className="text-warn">{t("step.gone")}</div>}
      {error && <div className="text-bad">{error}</div>}
      {(canLoad || (sessionId && table && !gone)) && (
        <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
          {canLoad && (
            <button type="button" onClick={loadMore} disabled={loading} className={buttonClass}>
              {loading && <Icon name="refresh" size={11} className="animate-spin" />}
              {loading ? t("step.loading") : t("step.showMore")}
            </button>
          )}
          {sessionId && table && !gone && (
            <button type="button" onClick={download} disabled={exporting} className={buttonClass}>
              <Icon name={exporting ? "refresh" : "download"} size={11} className={exporting ? "animate-spin" : ""} />
              {t("step.download")}
            </button>
          )}
        </div>
      )}
    </Section>
  );
}

function Attempts({ attempts, step }: { attempts: StepAttempt[]; step: StepState }) {
  const t = useT();
  return (
    <Section title={t("step.attempts")}>
      <ol className="space-y-1.5">
        {attempts.map((attempt) => (
          <li key={attempt.attempt} className="rounded-md border border-graphite-800 px-2 py-1.5">
            <div className="text-graphite-400">{t("step.attempt", { n: attempt.attempt })}</div>
            {attempt.error && <div className="mt-0.5 whitespace-pre-wrap break-words text-bad/90">{attempt.error}</div>}
            {attempt.arguments && (
              <div className="mt-1">
                <Arguments args={attempt.arguments} />
              </div>
            )}
          </li>
        ))}
      </ol>
      {(step.status === "finished" || step.status === "failed") && (
        <div className={step.status === "finished" ? "text-good" : "text-bad"}>
          {step.status === "finished" ? t("step.statusFixed") : t("step.statusFailed")}
        </div>
      )}
    </Section>
  );
}

export default function StepDetails({
  step,
  sessionId,
  tableName,
  stepNumber,
  onOpenStep,
}: {
  step: StepState;
  sessionId?: string;
  /** Название таблицы для пользователя (display_name из сводки). */
  tableName: (table: string) => string;
  /** Номер шага в списке плана; undefined — шага в этом ходе нет. */
  stepNumber: (stepId: number) => number | undefined;
  /** Раскрыть шаг, чей результат пришёл на вход, и прокрутить к нему. */
  onOpenStep: (stepId: number) => void;
}) {
  const t = useT();
  const tool = toolLabel(t, step.tool);
  const legacy = step.arguments === undefined && step.input === undefined;
  const attempts = step.attempts ?? [];

  return (
    <div className="mt-1 mb-2 ml-[21px] space-y-2.5 rounded-lg border border-graphite-800 bg-graphite-850/40 px-3 py-2 text-[12px]">
      <Section title={t("step.operation")}>
        {step.purpose && <div className="text-graphite-200">{step.purpose}</div>}
        {tool && (
          <div className="text-graphite-400">
            {tool}
            {tool !== step.tool && <span className="ml-1.5 font-mono text-[10.5px] text-graphite-500">{step.tool}</span>}
          </div>
        )}
        <Arguments args={step.arguments} />
        {step.code && step.code_language && <CodeBlock code={step.code} language={step.code_language} />}
      </Section>

      {(step.input !== undefined || step.input_table !== undefined) && (
        <Section title={t("step.input")}>
          <InputLine step={step} tableName={tableName} stepNumber={stepNumber} onOpenStep={onOpenStep} />
        </Section>
      )}

      {step.status === "failed" && step.error && (
        <Section title={t("step.error")}>
          <div className="whitespace-pre-wrap break-words text-bad">{step.error}</div>
        </Section>
      )}
      {attempts.length > 0 && <Attempts attempts={attempts} step={step} />}

      {step.status === "finished" && <Result step={step} sessionId={sessionId} />}
      {(step.status === "queued" || step.status === "started" || step.status === "fixing") && !legacy && (
        <div className="text-graphite-500">{t("step.noResultYet")}</div>
      )}
      {legacy && !(step.status === "failed" && step.error) && (
        <div className="text-graphite-500">{t("step.noDetails")}</div>
      )}
    </div>
  );
}
