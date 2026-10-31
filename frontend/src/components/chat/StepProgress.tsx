"use client";

/**
 * Прогресс хода по событиям progress: полная хронология этапов (планирование,
 * SQL-шаги, перепланирование, инсайт) с таймерами и активной стадией.
 * План показывается строками до выполнения: «в очереди» → «идёт» → «готово»
 * или «упал»; строка раскрывается — что шаг сделал с данными и что получилось
 * (StepDetails, #239). В свёрнутом виде: «N шагов · X с» (или «N из M плиток · X с»),
 * при раскрытии видна детальная разбивка по времени.
 * В режиме дашборда (#224) вместо плоского списка — плитки плана, у каждой свой
 * статус и свои шаги.
 * Остановленный или упавший ход с готовыми шагами — кнопка «Продолжить».
 */
import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import StepDetails from "@/components/chat/StepDetails";
import Icon from "@/components/ui/Icon";
import { useT, type Translate } from "@/i18n/useT";
import { tableTitle, useSessionDatabase } from "@/store/sessionStore";
import { useUiStore, type Locale } from "@/store/uiStore";
import type {
  PlannedStepSummary,
  PlanningSubstage,
  StepState,
  TimelineStage,
  TileIntent,
  TileState,
  TurnProgress,
} from "@/types/chat";

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

/** «3 из 5 шагов»: сколько шагов выполнено из запланированных. */
function stepsOf(locale: Locale, done: number, total: number): string {
  if (done >= total) return stepsLabel(locale, done);
  if (locale === "en") return `${done} of ${total} ${total === 1 ? "step" : "steps"}`;
  const mod10 = total % 10;
  const mod100 = total % 100;
  const genitive = mod10 === 1 && mod100 !== 11 ? "шага" : "шагов";
  return `${done} из ${total} ${genitive}`;
}

const PLANNING_SUBSTAGES: PlanningSubstage[] = ["semantic", "llm", "validation"];
const SUBSTAGE_LABEL = {
  semantic: "progress.substage.semantic",
  llm: "progress.substage.llm",
  validation: "progress.substage.validation",
} as const;

// Планы, которые уже полностью раскрыты: при повторном открытии прогресса
// не проигрываем анимацию заново.
const revealedPlans = new Set<string>();

/** Реальные фазы планирования от StepPlanner.plan: semantic -> llm -> validation. */
function ActiveSubstages({ substage }: { substage?: PlanningSubstage }) {
  const t = useT();
  if (!substage) return null;
  const current = PLANNING_SUBSTAGES.indexOf(substage);
  return (
    <ul className="mt-2 ml-4 pl-2.5 border-l border-graphite-800/80 space-y-1.5 transition-all duration-300">
      {PLANNING_SUBSTAGES.map((key, index) => {
        const done = index < current;
        const active = index === current;
        return (
          <li
            key={key}
            className={`flex items-center gap-2 text-[11px] transition-all duration-200 ${
              active
                ? "text-graphite-200 font-medium"
                : done
                ? "text-graphite-400"
                : "text-graphite-600"
            }`}
          >
            {done ? (
              <Icon name="check" size={11} className="text-good shrink-0" />
            ) : active ? (
              <span className="relative flex h-2 w-2 shrink-0 items-center justify-center">
                <span className="animate-ping motion-reduce:animate-none absolute inline-flex h-full w-full rounded-full bg-good/60 opacity-75" />
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-good" />
              </span>
            ) : (
              <span className="h-1.5 w-1.5 rounded-full bg-graphite-700 shrink-0 ml-[1px] mr-[1px]" />
            )}
            <span className="truncate">{t(SUBSTAGE_LABEL[key])}</span>
          </li>
        );
      })}
    </ul>
  );
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
}: {
  step: StepState;
  number: number;
  anchor: string;
  open: boolean;
  onToggle: () => void;
  details: ReactNode;
}) {
  const t = useT();
  const isRunning = step.status === "started" || step.status === "fixing";
  const icon =
    step.status === "finished" ? (
      <Icon name="database" size={13} className="text-good transition-transform duration-200 shrink-0" />
    ) : step.status === "failed" ? (
      <Icon name="x" size={13} className="text-bad shrink-0" />
    ) : step.status === "queued" ? (
      <QueuedDot />
    ) : (
      <Icon
        name="refresh"
        size={13}
        className={`animate-spin motion-reduce:animate-none shrink-0 ${
          step.status === "fixing" ? "text-warn" : "text-good"
        }`}
      />
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
        className="group flex w-full items-center gap-2 rounded text-left text-[12px] transition-all duration-200 hover:text-graphite-200"
      >
        <Icon
          name={open ? "chevronDown" : "chevronRight"}
          size={11}
          className="-mr-1 shrink-0 text-graphite-600 group-hover:text-graphite-400"
        />
        <span className="shrink-0">{icon}</span>
        <span className="w-4 shrink-0 text-right tabular-nums text-graphite-500">{number}.</span>
        <span
          className={`min-w-0 flex-1 truncate ${
            step.status === "queued"
              ? "text-graphite-500"
              : isRunning
              ? "text-graphite-200 font-medium"
              : "text-graphite-400"
          }`}
        >
          {step.purpose || step.tool || `#${step.step_id}`}
        </span>
        {note && (
          <span
            className={`shrink-0 text-[11px] font-medium ${
              step.status === "failed"
                ? "text-bad"
                : step.status === "queued"
                ? "text-graphite-500"
                : "text-warn"
            }`}
          >
            {note}
          </span>
        )}
        {step.elapsed_ms !== undefined && step.status !== "fixing" && (
          <span
            className="shrink-0 tabular-nums text-graphite-500 text-[11px] flex items-center gap-1"
            title={t("progress.sqlDuckDbTooltip")}
          >
            <span className="text-[10px] text-graphite-600 font-mono">SQL</span>
            <span>{duration(t, step.elapsed_ms)}</span>
          </span>
        )}
      </button>
      {open && details}
    </li>
  );
}


function StageRow({
  stage,
  stepOf,
  renderStep,
}: {
  stage: TimelineStage;
  stepOf: (stepId: number) => StepState | undefined;
  renderStep: (step: StepState) => ReactNode;
}) {
  const t = useT();
  const locale = useUiStore((s) => s.locale);
  // План открыт по умолчанию: статусы шагов теперь живут только здесь,
  // отдельных строк шагов в таймлайне больше нет.
  const [detailsOpen, setDetailsOpen] = useState(true);
  const isPlanStage = stage.stage === "planning" || stage.stage === "replanning";
  const plannedSteps: PlannedStepSummary[] = stage.planned_steps ?? [];
  const hasPlannedSteps = isPlanStage && plannedSteps.length > 0;
  const failed = stage.status === "failed";

  // План приходит одним structured-ответом LLM. Раскрываем шаги по одному,
  // чтобы список не появлялся целиком в один кадр.
  const planKey = `${stage.id}:${plannedSteps.length}`;
  const [visibleSteps, setVisibleSteps] = useState(() =>
    revealedPlans.has(planKey) ? plannedSteps.length : 0
  );
  useEffect(() => {
    if (plannedSteps.length === 0) return;
    if (revealedPlans.has(planKey)) {
      setVisibleSteps(plannedSteps.length);
      return;
    }
    setVisibleSteps(0);
    let shown = 0;
    const timer = setInterval(() => {
      shown += 1;
      setVisibleSteps(shown);
      if (shown >= plannedSteps.length) {
        revealedPlans.add(planKey);
        clearInterval(timer);
      }
    }, 140);
    return () => clearInterval(timer);
  }, [planKey, plannedSteps.length]);

  // Состояние шага из прогресса; план пришёл раньше step-событий — показываем
  // его строкой «в очереди».
  const rowFor = (step: PlannedStepSummary): StepState =>
    stepOf(step.step_id) ?? {
      step_id: step.step_id,
      tool: step.tool,
      purpose: step.purpose,
      status: "queued",
    };
  const shownSteps = plannedSteps.slice(0, visibleSteps);

  const label = t(stage.labelKey);
  const icon = failed ? (
    <Icon name="x" size={13} className="text-bad shrink-0" />
  ) : stage.stage === "insight" ? (
    <Icon name="sparkle" size={13} className="text-good shrink-0" />
  ) : stage.stage === "chart" ? (
    <Icon name="chart" size={13} className="text-good shrink-0" />
  ) : stage.stage === "dashboard" ? (
    <Icon name="layout" size={13} className="text-good shrink-0" />
  ) : (
    <Icon name="check" size={13} className="text-good shrink-0" />
  );

  const content = (
    <>
      <span className="shrink-0">{icon}</span>
      <span
        className={`min-w-0 flex-1 truncate transition-colors ${
          failed ? "text-bad" : "text-graphite-300 group-hover:text-graphite-200"
        }`}
      >
        {label}
      </span>
      {hasPlannedSteps && (
        <span className="shrink-0 text-[10px] text-graphite-400 bg-graphite-800/80 px-1.5 py-0.5 rounded border border-graphite-700/50 inline-flex items-center gap-1">
          <span>{stepsLabel(locale, plannedSteps.length)}</span>
          <Icon
            name={detailsOpen ? "chevronDown" : "chevronRight"}
            size={10}
            className="shrink-0 text-graphite-400"
          />
        </span>
      )}
      {stage.elapsed_ms !== undefined && (
        <span className="shrink-0 tabular-nums text-graphite-500 text-[11px]">
          {duration(t, stage.elapsed_ms)}
        </span>
      )}
    </>
  );

  return (
    <li
      className="text-[12px] transition-all duration-200 space-y-1"
      title={failed ? stage.error : undefined}
    >
      {hasPlannedSteps ? (
        <button
          type="button"
          aria-expanded={detailsOpen}
          onClick={() => setDetailsOpen(!detailsOpen)}
          className="group flex w-full items-center gap-2 text-left select-none rounded px-0.5 -mx-0.5 hover:bg-graphite-800/30"
        >
          {content}
        </button>
      ) : (
        <div className="flex items-center gap-2">{content}</div>
      )}

      {/* Раскрытый список того, что именно спланировала модель */}
      {hasPlannedSteps && detailsOpen && (
        <div className="ml-4 pl-2.5 border-l border-graphite-800/80 space-y-1 my-1">
          <div className="text-[11px] text-graphite-500 font-medium">
            {t("progress.plannedSteps")}
          </div>
          <ul className="space-y-1">{shownSteps.map((step) => renderStep(rowFor(step)))}</ul>
        </div>
      )}
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
}: {
  progress: TurnProgress;
  sessionId?: string;
  onResume?: () => void;
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
  const stepOf = useCallback(
    (stepId: number) => steps.find((step) => step.step_id === stepId),
    [steps],
  );

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

  // Если есть timeline — отображаем всю хронологию; иначе только steps (fallback)
  const timelineEntries = progress.timeline || [];
  // Шаги, которые уже показаны в раскрытом плане, не дублируем отдельными
  // строками: статус, время и ошибка живут внутри плана.
  const plannedStepIds = new Set<number>();
  for (const entry of timelineEntries) {
    if (entry.kind === "stage" && entry.planned_steps) {
      for (const step of entry.planned_steps) plannedStepIds.add(step.step_id);
    }
  }
  const isPlannedStep = (step: StepState) => plannedStepIds.has(step.step_id);
  const unplannedSteps = progress.steps.filter((step) => !isPlannedStep(step));
  const completedEntries = (
    finished
      ? timelineEntries
      : timelineEntries.filter((e) => e.kind === "step" || e.finished_at !== undefined)
  ).filter((entry) => entry.kind !== "step" || !isPlannedStep(entry.step));

  // Активная стадия: таймер нужен только пока она реально идёт.
  const activeStageEntry = progress.timeline?.find(
    (e): e is TimelineStage => e.kind === "stage" && e.finished_at === undefined
  );
  const activeStageId = activeStageEntry?.id;

  // Живой секундомер, пока есть открытая стадия или пока ход идёт
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (finished) return;
    setNow(Date.now());
    const interval = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(interval);
  }, [finished]);

  const totalElapsed = Math.max(0, (progress.finished_at ?? now) - progress.started_at);
  const totalSteps = Math.max(progress.total_steps ?? progress.steps.length, progress.steps.length);
  // В steps лежат и шаги плана «в очереди»: выполненные считаем без них.
  const doneSteps = progress.steps.filter((step) => step.status !== "queued").length;
  const stepsText = stepsOf(locale, doneSteps, totalSteps);


  const collapsedLabel = tiles
    ? t("progress.tiles", {
        done: tiles.filter((tile) => tile.status === "finished").length,
        total: tiles.length,
      })
    : stepsText;

  if (finished && !expanded) {
    return (
      <div>
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="inline-flex items-center gap-1.5 text-[12px] text-graphite-500 hover:text-graphite-300 hover:bg-graphite-800/40 px-1.5 py-0.5 -ml-1.5 rounded transition-all"
        >
          <Icon name="chevronRight" size={12} className="shrink-0" />
          <span>{collapsedLabel}</span>
          <span>·</span>
          <span className="tabular-nums">{t("progress.seconds", { s: seconds(totalElapsed) })}</span>
        </button>
        {onResume ? (
          <ResumeBar onResume={onResume} lost={progress.lost} />
        ) : (
          progress.lost && <div className="mt-1 text-[12px] text-warn">{t("progress.lost")}</div>
        )}
      </div>
    );
  }

  // Название текущего активного этапа
  const activeLabel =
    progress.toolgen || progress.stage === "toolgen"
      ? t("progress.toolgen")
      : progress.planning
      ? t("progress.planning")
      : progress.stage === "replanning"
      ? t("progress.replanning")
      : progress.stage === "insight"
      ? t("progress.insight")
      : progress.stage === "chart"
      ? tiles
        ? t("progress.chooseCharts")
        : t("progress.chart")
      : progress.stage === "dashboard"
      ? t("progress.dashboard")
      : null;
  const quiet = !finished && now - (progress.last_event_at ?? progress.started_at) > QUIET_MS;

  const activeStageStartedAt = activeStageEntry?.started_at ?? progress.started_at;
  const activeStageElapsed = Math.max(0, now - activeStageStartedAt);

  return (
    <div className="text-[12px] text-graphite-400 space-y-1.5">
      {finished && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="mb-1 inline-flex items-center gap-1.5 text-graphite-500 hover:text-graphite-300 hover:bg-graphite-800/40 px-1.5 py-0.5 -ml-1.5 rounded transition-all"
        >
          <Icon name="chevronDown" size={12} className="shrink-0" />
          <span>{collapsedLabel}</span>
          <span>·</span>
          <span className="tabular-nums">{t("progress.seconds", { s: seconds(totalElapsed) })}</span>
        </button>
      )}

      {progress.outlining && <div className="animate-pulse">{t("progress.outline")}</div>}
      {progress.toolgen && <div className="animate-pulse">{t("progress.toolgen")}</div>}

      {tiles && tiles.length > 0 ? (
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
      ) : (
        /* Список выполненных этапов и шагов */
        completedEntries.length > 0 ? (
          <ul className="space-y-1">
            {completedEntries.map((entry) =>
              entry.kind === "step" ? (
                renderStep(entry.step)
              ) : (
                <StageRow key={entry.id} stage={entry} stepOf={stepOf} renderStep={renderStep} />
              )
            )}
          </ul>
        ) : (
          unplannedSteps.length > 0 && (
            <ul className="space-y-1">{unplannedSteps.map(renderStep)}</ul>
          )
        )
      )}

      {/* Активная стадия: спиннер и реальные фазы планирования */}
      {!finished && activeLabel && (
        <div className="space-y-1 transition-all duration-300 ease-out">
          <div className="flex items-center gap-2 text-graphite-200" data-active-stage={activeStageId}>
            <span className="relative flex h-2 w-2 shrink-0 items-center justify-center">
              <span className="animate-ping motion-reduce:animate-none absolute inline-flex h-full w-full rounded-full bg-good/60 opacity-75" />
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-good" />
            </span>
            <Icon
              name="refresh"
              size={13}
              className="animate-spin motion-reduce:animate-none text-graphite-400 shrink-0"
            />
            <span className="text-[12px] font-medium text-graphite-200 tracking-wide">{activeLabel}</span>
            <span className="shrink-0 tabular-nums text-[11px] text-graphite-400 bg-graphite-800/70 px-1.5 py-0.5 rounded border border-graphite-700/50 shadow-sm">
              {t("progress.seconds", { s: seconds(activeStageElapsed) })}
            </span>
          </div>

          {/* Только реальные фазы из StepPlanner.plan, без таймерных догадок */}
          {(progress.planning || progress.stage === "replanning") && (
            <ActiveSubstages substage={activeStageEntry?.substage} />
          )}
        </div>
      )}

      {quiet && <div className="mt-1 text-warn">{t("progress.stillWorking")}</div>}
      {finished && onResume && <ResumeBar onResume={onResume} lost={progress.lost} />}
    </div>
  );
}
