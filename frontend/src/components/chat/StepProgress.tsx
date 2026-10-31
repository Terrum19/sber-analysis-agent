"use client";

/**
 * Прогресс хода по событиям progress: «Планирую…», затем весь план
 * пронумерованными строками (purpose, статус, время): «в очереди» → «идёт» →
 * «готово» или «упал». Строка раскрывается по клику — что шаг сделал с данными
 * и что получилось (StepDetails, #239). Когда ход закончен — свёрнут в строку
 * «N шагов · X с», по клику раскрывается. В режиме дашборда (#224) вместо
 * плоского списка — плитки плана, у каждой свой статус и свои шаги.
 * Остановленный или упавший ход с готовыми шагами — кнопка «Продолжить».
 * В режиме «Проверка» (A-29) у шага — значок проверки его результата и
 * найденные аномалии; в исправлении ответа новые шаги помечены «добавлено проверкой».
 */
import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import StepDetails from "@/components/chat/StepDetails";
import Icon from "@/components/ui/Icon";
import { useT, type Translate } from "@/i18n/useT";
import { tableTitle, useSessionDatabase } from "@/store/sessionStore";
import { useUiStore, type Locale } from "@/store/uiStore";
import type { StepState, TileIntent, TileState, TurnProgress } from "@/types/chat";

/** Тишина дольше этого — показываем «Всё ещё работаю…». */
const QUIET_MS = 20_000;

const INTENT_ICON: Record<TileIntent, string> = {
  kpi: "#",
  compare: "▥",
  trend: "↗",
  distribution: "▁▃▅",
  relation: "⁘",
  share: "◔",
};

function stepsLabel(locale: Locale, count: number): string {
  if (locale === "en") return `${count} ${count === 1 ? "step" : "steps"}`;
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} шаг`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} шага`;
  return `${count} шагов`;
}

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}`;
}

function duration(t: Translate, ms: number): string {
  if (ms < 1000) return t("progress.milliseconds", { ms: Math.max(1, Math.round(ms)) });
  return t("progress.seconds", { s: seconds(ms) });
}

function QueuedDot() {
  return <span className="inline-block h-[13px] w-[13px] rounded-full border border-graphite-600" />;
}

function StepRow({
  step,
  number,
  anchor,
  open,
  onToggle,
  details,
  added,
}: {
  step: StepState;
  number: number;
  anchor: string;
  open: boolean;
  onToggle: () => void;
  details: ReactNode;
  /** Шаг дописан проверкой ответа (режим «Проверка»). */
  added?: boolean;
}) {
  const t = useT();
  const icon =
    step.status === "finished" ? (
      <Icon name="check" size={13} className="text-good" />
    ) : step.status === "failed" ? (
      <Icon name="x" size={13} className="text-bad" />
    ) : step.status === "queued" ? (
      <QueuedDot />
    ) : (
      <Icon name="refresh" size={13} className={`animate-spin ${step.status === "fixing" ? "text-warn" : "text-graphite-400"}`} />
    );
  const note =
    step.status === "fixing"
      ? t("progress.fixing", { attempt: step.attempt ?? 1 })
      : step.status === "failed"
      ? t("progress.failed")
      : step.status === "queued"
      ? t("progress.queued")
      : step.fixed
      ? t("progress.fixed")
      : "";
  return (
    <li id={anchor} className="scroll-mt-4">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="group flex w-full items-center gap-2 rounded text-left hover:text-graphite-200"
      >
        <Icon
          name={open ? "chevronDown" : "chevronRight"}
          size={11}
          className="-mr-1 shrink-0 text-graphite-600 group-hover:text-graphite-400"
        />
        <span className="shrink-0">{icon}</span>
        <span className="w-4 shrink-0 text-right tabular-nums text-graphite-500">{number}.</span>
        <span className={`min-w-0 flex-1 truncate ${step.status === "queued" ? "text-graphite-500" : ""}`}>
          {step.purpose || step.tool || `#${step.step_id}`}
        </span>
        {note && (
          <span
            className={`shrink-0 ${
              step.status === "failed" ? "text-bad" : step.status === "queued" ? "text-graphite-500" : "text-warn"
            }`}
          >
            {note}
          </span>
        )}
        {added && <span className="shrink-0 rounded bg-graphite-800 px-1 text-[10.5px] text-graphite-300">{t("verify.added")}</span>}
        {step.check?.status === "started" && (
          <span className="inline-flex shrink-0 items-center gap-1 text-graphite-500">
            <Icon name="refresh" size={11} className="animate-spin" />
            {t("verify.checking")}
          </span>
        )}
        {step.check?.status === "finished" && step.check.findings.length === 0 && (
          <span className="shrink-0 text-good" title={t("verify.stepClean")}>
            <Icon name="search" size={11} />
          </span>
        )}
        {step.check?.status === "finished" && step.check.findings.length > 0 && (
          <span className="inline-flex shrink-0 items-center gap-0.5 text-warn" title={step.check.findings.map((f) => f.message).join("\n")}>
            <Icon name="alertTriangle" size={11} />
            {step.check.findings.length}
          </span>
        )}
        {step.elapsed_ms !== undefined && step.status !== "fixing" && (
          <span className="shrink-0 tabular-nums text-graphite-500">{duration(t, step.elapsed_ms)}</span>
        )}
      </button>
      {step.check?.status === "finished" && step.check.findings.length > 0 && (
        <ul className="ml-[3.25rem] mt-0.5 space-y-0.5 text-[11.5px] text-warn/90">
          {step.check.findings.map((finding, index) => (
            <li key={index}>⚠ {finding.message}</li>
          ))}
        </ul>
      )}
      {open && details}
    </li>
  );
}

function TileRow({
  tile,
  steps,
  open,
  onToggle,
  renderStep,
}: {
  tile: TileState;
  steps: StepState[];
  open: boolean;
  onToggle: () => void;
  renderStep: (step: StepState) => ReactNode;
}) {
  const t = useT();
  // Шаги идущей плитки видны сразу, готовой — по клику
  const showSteps = steps.length > 0 && (open || tile.status === "started");
  const icon =
    tile.status === "finished" ? (
      <Icon name="check" size={13} className="text-good" />
    ) : tile.status === "failed" ? (
      <Icon name="x" size={13} className="text-bad" />
    ) : tile.status === "started" ? (
      <Icon name="refresh" size={13} className="animate-spin text-graphite-400" />
    ) : (
      <QueuedDot />
    );
  return (
    <li className="rounded-lg border border-graphite-800 bg-graphite-850/60 px-2.5 py-1.5">
      <button
        onClick={onToggle}
        disabled={steps.length === 0}
        className="flex w-full items-center gap-2 text-left disabled:cursor-default"
      >
        <span className="shrink-0">{icon}</span>
        {tile.intent && (
          <span className="w-7 shrink-0 text-center font-mono text-[11px] text-graphite-500" title={tile.intent}>
            {INTENT_ICON[tile.intent]}
          </span>
        )}
        <span className={`min-w-0 flex-1 truncate ${tile.status === "queued" ? "text-graphite-500" : "text-graphite-200"}`}>
          {tile.title}
        </span>
        {tile.status === "queued" && <span className="shrink-0 text-graphite-500">{t("progress.tileQueued")}</span>}
        {tile.status === "failed" && <span className="shrink-0 text-bad">{t("progress.failed")}</span>}
        {tile.elapsed_ms !== undefined && tile.status !== "started" && (
          <span className="shrink-0 tabular-nums text-graphite-500">{duration(t, tile.elapsed_ms)}</span>
        )}
      </button>
      {tile.status === "failed" && tile.error && (
        <div className="mt-1 pl-6 text-[11.5px] text-bad/90 line-clamp-2" title={tile.error}>
          {tile.error}
        </div>
      )}
      {showSteps && (
        <ul className="mt-1 space-y-1 border-l border-graphite-800 pl-3 ml-1.5">
          {steps.map(renderStep)}
        </ul>
      )}
    </li>
  );
}

/** Секунды с начала хода, обновляются раз в секунду, пока ход идёт. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

/** «Прервано» и кнопка «Продолжить» под прогрессом остановленного хода. */
function ResumeBar({ onResume, lost }: { onResume: () => void; lost?: boolean }) {
  const t = useT();
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-2">
      <span className="text-[12px] text-warn">{t(lost ? "progress.lost" : "progress.interrupted")}</span>
      <button
        onClick={onResume}
        title={t("progress.resumeHint")}
        className="inline-flex items-center gap-1 rounded-md border border-graphite-700 px-2 py-0.5 text-[12px] text-graphite-200 hover:border-graphite-500 hover:text-graphite-50"
      >
        <Icon name="refresh" size={12} />
        {t("progress.resume")}
      </button>
    </div>
  );
}

export default function StepProgress({
  progress,
  sessionId,
  onResume,
  knownSteps,
}: {
  progress: TurnProgress;
  sessionId?: string;
  onResume?: () => void;
  /** Исправление ответа: шаги первого прогона; остальные помечены «добавлено проверкой». */
  knownSteps?: ReadonlySet<number>;
}) {
  const t = useT();
  const locale = useUiStore((s) => s.locale);
  const database = useSessionDatabase(sessionId);
  const [expanded, setExpanded] = useState(false);
  const [openSteps, setOpenSteps] = useState<ReadonlySet<number>>(() => new Set());
  const [openTiles, setOpenTiles] = useState<ReadonlySet<number>>(() => new Set());
  const [scrollTo, setScrollTo] = useState<number | null>(null);
  const anchorPrefix = useId();
  const finished = progress.finished_at !== undefined;
  const now = useNow(!finished);
  const tiles = progress.tiles;
  const steps = progress.steps;

  // Номер шага в его списке (в плитке — свой счёт): step_id продолжают прошлые ходы
  const numbers = useMemo(() => {
    const counters = new Map<number | undefined, number>();
    return new Map(
      steps.map((step) => {
        const number = (counters.get(step.tile_id) ?? 0) + 1;
        counters.set(step.tile_id, number);
        return [step.step_id, number] as const;
      }),
    );
  }, [steps]);
  const anchor = (stepId: number) => `${anchorPrefix}-step-${stepId}`;
  const tableName = useCallback((table: string) => tableTitle(database, table), [database]);
  const stepNumber = useCallback((stepId: number) => numbers.get(stepId), [numbers]);

  const toggle = (set: ReadonlySet<number>, id: number) => {
    const next = new Set(set);
    if (!next.delete(id)) next.add(id);
    return next;
  };
  // Ссылка «Результат шага N»: раскрыть шаг (и его плитку) и прокрутить к нему
  const openStep = useCallback(
    (stepId: number) => {
      const tileId = steps.find((step) => step.step_id === stepId)?.tile_id;
      setOpenSteps((set) => new Set(set).add(stepId));
      if (tileId !== undefined) setOpenTiles((set) => new Set(set).add(tileId));
      setScrollTo(stepId);
    },
    [steps],
  );
  useEffect(() => {
    if (scrollTo === null) return;
    document.getElementById(`${anchorPrefix}-step-${scrollTo}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    setScrollTo(null);
  }, [scrollTo, anchorPrefix]);

  const renderStep = (step: StepState) => (
    <StepRow
      key={step.step_id}
      step={step}
      number={numbers.get(step.step_id) ?? step.step_id}
      anchor={anchor(step.step_id)}
      open={openSteps.has(step.step_id)}
      onToggle={() => setOpenSteps((set) => toggle(set, step.step_id))}
      added={knownSteps !== undefined && !knownSteps.has(step.step_id)}
      details={
        <StepDetails
          step={step}
          sessionId={sessionId}
          tableName={tableName}
          stepNumber={stepNumber}
          onOpenStep={openStep}
        />
      }
    />
  );

  if (finished && !expanded) {
    const total = (progress.finished_at ?? progress.started_at) - progress.started_at;
    const label = tiles
      ? t("progress.tiles", {
          done: tiles.filter((tile) => tile.status === "finished").length,
          total: tiles.length,
        })
      : stepsLabel(locale, progress.steps.length);
    return (
      <div>
        <button
          onClick={() => setExpanded(true)}
          className="inline-flex items-center gap-1 text-[12px] text-graphite-500 hover:text-graphite-300"
        >
          <Icon name="chevronRight" size={12} />
          {label} · {t("progress.seconds", { s: seconds(total) })}
        </button>
        {onResume ? (
          <ResumeBar onResume={onResume} lost={progress.lost} />
        ) : (
          progress.lost && <div className="mt-1 text-[12px] text-warn">{t("progress.lost")}</div>
        )}
      </div>
    );
  }

  const stageLabel =
    progress.stage === "insight"
      ? t("progress.insight")
      : progress.stage === "chart"
      ? tiles
        ? t("progress.chooseCharts")
        : t("progress.chart")
      : progress.stage === "dashboard"
      ? t("progress.dashboard")
      : null;
  const quiet = !finished && now - (progress.last_event_at ?? progress.started_at) > QUIET_MS;

  return (
    <div className="text-[12px] text-graphite-400">
      {finished && (
        <button
          onClick={() => setExpanded(false)}
          className="mb-1 inline-flex items-center gap-1 text-graphite-500 hover:text-graphite-300"
        >
          <Icon name="chevronDown" size={12} />
          {stepsLabel(locale, progress.steps.length)}
        </button>
      )}
      {!finished && (
        <div className="mb-1 tabular-nums text-graphite-500">{t("progress.seconds", { s: Math.floor((now - progress.started_at) / 1000) })}</div>
      )}
      {progress.outlining && <div className="animate-pulse">{t("progress.outline")}</div>}
      {progress.planning && !tiles && <div className="animate-pulse">{t("progress.planning")}</div>}
      {tiles && tiles.length > 0 && (
        <ul className="space-y-1.5">
          {tiles.map((tile) => (
            <TileRow
              key={tile.tile_id}
              tile={tile}
              steps={steps.filter((step) => step.tile_id === tile.tile_id)}
              open={openTiles.has(tile.tile_id)}
              onToggle={() => setOpenTiles((set) => toggle(set, tile.tile_id))}
              renderStep={renderStep}
            />
          ))}
        </ul>
      )}
      {!tiles && steps.length > 0 && <ul className="space-y-1">{steps.map(renderStep)}</ul>}
      {!finished && stageLabel && <div className="mt-1 animate-pulse">{stageLabel}</div>}
      {quiet && <div className="mt-1 text-warn">{t("progress.stillWorking")}</div>}
      {finished && onResume && <ResumeBar onResume={onResume} lost={progress.lost} />}
    </div>
  );
}
