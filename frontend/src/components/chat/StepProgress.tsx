"use client";

/**
 * Прогресс хода по событиям progress: полная хронология этапов (планирование,
 * SQL-шаги, перепланирование, инсайт) с таймерами и активной стадией.
 * В свёрнутом виде: «N шагов · X с», при раскрытии видна детальная разбивка по времени.
 */
import { useEffect, useState } from "react";
import Icon from "@/components/ui/Icon";
import { useT, type Translate } from "@/i18n/useT";
import { useUiStore, type Locale } from "@/store/uiStore";
import type { StepState, TimelineStage, TurnProgress } from "@/types/chat";

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

function ActiveSubstages({
  isReplanning,
  elapsedSec,
}: {
  isReplanning: boolean;
  elapsedSec: number;
}) {
  const t = useT();
  const prefix = isReplanning ? "progress.replanning" : "progress.planning";
  const substages = [
    { key: `${prefix}.substage1`, doneAfter: 7 },
    { key: `${prefix}.substage2`, doneAfter: 20 },
    { key: `${prefix}.substage3`, doneAfter: 40 },
    { key: `${prefix}.substage4`, doneAfter: Infinity },
  ];

  return (
    <ul className="mt-2 ml-4 pl-2.5 border-l border-graphite-800/80 space-y-1.5 transition-all duration-300">
      {substages.map((sub, idx) => {
        const isDone = elapsedSec >= sub.doneAfter;
        const isCurrent =
          !isDone && (idx === 0 || elapsedSec >= substages[idx - 1].doneAfter);

        return (
          <li
            key={sub.key}
            className={`flex items-center gap-2 text-[11px] transition-all duration-200 ${
              isCurrent
                ? "text-graphite-200 font-medium"
                : isDone
                ? "text-graphite-400"
                : "text-graphite-600"
            }`}
          >
            {isDone ? (
              <Icon name="check" size={11} className="text-good shrink-0" />
            ) : isCurrent ? (
              <span className="relative flex h-2 w-2 shrink-0 items-center justify-center">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-good/60 opacity-75" />
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-good" />
              </span>
            ) : (
              <span className="h-1.5 w-1.5 rounded-full bg-graphite-700 shrink-0 ml-[1px] mr-[1px]" />
            )}
            <span className="truncate">{t(sub.key as any)}</span>
          </li>
        );
      })}
    </ul>
  );
}

function StageRow({ stage }: { stage: TimelineStage }) {
  const t = useT();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const isPlanStage = stage.stage === "planning" || stage.stage === "replanning";
  const hasPlannedSteps = isPlanStage && !!stage.planned_steps && stage.planned_steps.length > 0;

  const label = t(stage.labelKey as any) || stage.stage;
  const icon =
    stage.stage === "insight" ? (
      <Icon name="sparkle" size={13} className="text-good shrink-0" />
    ) : stage.stage === "chart" ? (
      <Icon name="chart" size={13} className="text-good shrink-0" />
    ) : stage.stage === "dashboard" ? (
      <Icon name="layout" size={13} className="text-good shrink-0" />
    ) : (
      <Icon name="check" size={13} className="text-good shrink-0" />
    );

  return (
    <li className="text-[12px] transition-all duration-200 space-y-1">
      <div
        className={`flex items-center gap-2 ${
          hasPlannedSteps ? "cursor-pointer select-none group" : ""
        }`}
        onClick={hasPlannedSteps ? () => setDetailsOpen(!detailsOpen) : undefined}
      >
        <span className="shrink-0">{icon}</span>
        <span className="min-w-0 flex-1 truncate text-graphite-300 group-hover:text-graphite-200 transition-colors">
          {label}
        </span>
        {hasPlannedSteps && (
          <span className="shrink-0 text-[10px] text-graphite-400 hover:text-graphite-200 bg-graphite-800/80 px-1.5 py-0.5 rounded border border-graphite-700/50 inline-flex items-center gap-1">
            <span>{stepsLabel(useUiStore.getState().locale, stage.planned_steps!.length)}</span>
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
      </div>

      {/* Раскрытый список того, что именно спланировала модель */}
      {hasPlannedSteps && detailsOpen && (
        <div className="ml-4 pl-2.5 border-l border-graphite-800/80 space-y-1 my-1">
          <div className="text-[11px] text-graphite-500 font-medium">
            {t("progress.plannedSteps")}
          </div>
          <ul className="space-y-1">
            {stage.planned_steps!.map((step) => (
              <li
                key={step.step_id}
                className="flex items-center gap-1.5 text-[11px] text-graphite-400"
              >
                <span className="h-1 w-1 rounded-full bg-good shrink-0" />
                <span className="text-graphite-300 font-medium">
                  #{step.step_id + 1}
                </span>
                <span className="truncate">{step.purpose}</span>
                {step.tool && (
                  <span className="text-[10px] font-mono text-graphite-500">
                    ({step.tool})
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

function StepRow({ step }: { step: StepState }) {
  const t = useT();
  const isRunning = step.status === "started" || step.status === "fixing";
  const icon =
    step.status === "finished" ? (
      <Icon name="database" size={13} className="text-good transition-transform duration-200 shrink-0" />
    ) : step.status === "failed" ? (
      <Icon name="x" size={13} className="text-bad shrink-0" />
    ) : (
      <Icon
        name="refresh"
        size={13}
        className={`animate-spin shrink-0 ${step.status === "fixing" ? "text-warn" : "text-good"}`}
      />
    );
  const note =
    step.status === "fixing"
      ? t("progress.fixing", { attempt: step.attempt ?? 1 })
      : step.status === "failed"
      ? t("progress.failed")
      : step.fixed
      ? t("progress.fixed")
      : "";

  return (
    <li
      className="flex items-center gap-2 text-[12px] transition-all duration-200"
      title={step.error || undefined}
    >
      <span className="shrink-0">{icon}</span>
      <span className={`min-w-0 flex-1 truncate ${isRunning ? "text-graphite-200 font-medium" : "text-graphite-400"}`}>
        {step.purpose || step.tool || `#${step.step_id}`}
      </span>
      {note && (
        <span className={`shrink-0 text-[11px] font-medium ${step.status === "failed" ? "text-bad" : "text-warn"}`}>
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
    </li>
  );
}

export default function StepProgress({ progress }: { progress: TurnProgress }) {
  const t = useT();
  const locale = useUiStore((s) => s.locale);
  const [expanded, setExpanded] = useState(false);
  const finished = progress.finished_at !== undefined;

  // Живой секундомер, пока генерация продолжается
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (finished) return;
    const interval = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(interval);
  }, [finished]);

  const totalElapsed = Math.max(0, (progress.finished_at ?? now) - progress.started_at);

  if (finished && !expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        className="inline-flex items-center gap-1.5 text-[12px] text-graphite-500 hover:text-graphite-300 hover:bg-graphite-800/40 px-1.5 py-0.5 -ml-1.5 rounded transition-all"
      >
        <Icon name="chevronRight" size={12} className="shrink-0" />
        <span>{stepsLabel(locale, progress.steps.length)}</span>
        <span>·</span>
        <span className="tabular-nums">{t("progress.seconds", { s: seconds(totalElapsed) })}</span>
      </button>
    );
  }

  // Название текущего активного этапа
  const activeLabel =
    progress.planning
      ? t("progress.planning")
      : progress.stage === "replanning"
      ? t("progress.replanning")
      : progress.stage === "insight"
      ? t("progress.insight")
      : progress.stage === "chart"
      ? t("progress.chart")
      : progress.stage === "dashboard"
      ? t("progress.dashboard")
      : null;

  // Если есть timeline — отображаем всю хронологию; иначе только steps (fallback)
  const timelineEntries = progress.timeline || [];
  const completedEntries = finished
    ? timelineEntries
    : timelineEntries.filter((e) => e.kind === "step" || e.finished_at !== undefined);

  // Замер времени текущей активной стадии
  const activeStageEntry = progress.timeline?.find(
    (e) => e.kind === "stage" && e.finished_at === undefined
  );
  const activeStageStartedAt =
    (activeStageEntry && activeStageEntry.kind === "stage"
      ? activeStageEntry.started_at
      : undefined) ?? progress.started_at;
  const activeStageElapsed = Math.max(0, now - activeStageStartedAt);

  return (
    <div className="text-[12px] text-graphite-400 space-y-1.5">
      {finished && (
        <button
          onClick={() => setExpanded(false)}
          className="mb-1 inline-flex items-center gap-1.5 text-graphite-500 hover:text-graphite-300 hover:bg-graphite-800/40 px-1.5 py-0.5 -ml-1.5 rounded transition-all"
        >
          <Icon name="chevronDown" size={12} className="shrink-0" />
          <span>{stepsLabel(locale, progress.steps.length)}</span>
          <span>·</span>
          <span className="tabular-nums">{t("progress.seconds", { s: seconds(totalElapsed) })}</span>
        </button>
      )}

      {/* Список выполненных этапов и шагов */}
      {completedEntries.length > 0 ? (
        <ul className="space-y-1">
          {completedEntries.map((entry) =>
            entry.kind === "step" ? (
              <StepRow key={entry.id} step={entry.step} />
            ) : (
              <StageRow key={entry.id} stage={entry} />
            )
          )}
        </ul>
      ) : (
        progress.steps.length > 0 && (
          <ul className="space-y-1">
            {progress.steps.map((step) => (
              <StepRow key={step.step_id} step={step} />
            ))}
          </ul>
        )
      )}

      {/* Активная стадия со спиннером, светящейся точкой, подпунктами и живым секундомером */}
      {!finished && activeLabel && (
        <div className="space-y-1 transition-all duration-300 ease-out">
          <div className="flex items-center gap-2 text-graphite-200">
            <span className="relative flex h-2 w-2 shrink-0 items-center justify-center">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-good/60 opacity-75" />
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-good" />
            </span>
            <Icon name="refresh" size={13} className="animate-spin text-graphite-400 shrink-0" />
            <span className="text-[12px] font-medium text-graphite-200 tracking-wide">{activeLabel}</span>
            <span className="shrink-0 tabular-nums text-[11px] text-graphite-400 bg-graphite-800/70 px-1.5 py-0.5 rounded border border-graphite-700/50 shadow-sm">
              {t("progress.seconds", { s: seconds(activeStageElapsed) })}
            </span>
          </div>

          {/* Подпункты планирования и уточнения плана */}
          {(progress.planning || progress.stage === "replanning") && (
            <ActiveSubstages
              isReplanning={progress.stage === "replanning"}
              elapsedSec={Math.floor(activeStageElapsed / 1000)}
            />
          )}
        </div>
      )}
    </div>
  );
}
