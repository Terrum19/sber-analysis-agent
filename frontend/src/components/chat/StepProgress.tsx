"use client";

/**
 * Прогресс хода по событиям progress: «Планирую…», затем список шагов
 * (purpose, статус, время). Когда ход закончен — свёрнут в строку
 * «N шагов · X с», по клику раскрывается.
 */
import { useState } from "react";
import Icon from "@/components/ui/Icon";
import { useT, type Translate } from "@/i18n/useT";
import { useUiStore, type Locale } from "@/store/uiStore";
import type { StepState, TurnProgress } from "@/types/chat";

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

function StepRow({ step }: { step: StepState }) {
  const t = useT();
  const icon =
    step.status === "finished" ? (
      <Icon name="check" size={13} className="text-good" />
    ) : step.status === "failed" ? (
      <Icon name="x" size={13} className="text-bad" />
    ) : (
      <Icon name="refresh" size={13} className={`animate-spin ${step.status === "fixing" ? "text-warn" : "text-graphite-400"}`} />
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
    <li className="flex items-center gap-2" title={step.error || undefined}>
      <span className="shrink-0">{icon}</span>
      <span className="min-w-0 flex-1 truncate">{step.purpose || step.tool || `#${step.step_id}`}</span>
      {note && <span className={`shrink-0 ${step.status === "failed" ? "text-bad" : "text-warn"}`}>{note}</span>}
      {step.elapsed_ms !== undefined && step.status !== "fixing" && (
        <span className="shrink-0 tabular-nums text-graphite-500">{duration(t, step.elapsed_ms)}</span>
      )}
    </li>
  );
}

export default function StepProgress({ progress }: { progress: TurnProgress }) {
  const t = useT();
  const locale = useUiStore((s) => s.locale);
  const [expanded, setExpanded] = useState(false);
  const finished = progress.finished_at !== undefined;

  if (finished && !expanded) {
    const total = (progress.finished_at ?? progress.started_at) - progress.started_at;
    return (
      <button
        onClick={() => setExpanded(true)}
        className="inline-flex items-center gap-1 text-[12px] text-graphite-500 hover:text-graphite-300"
      >
        <Icon name="chevronRight" size={12} />
        {stepsLabel(locale, progress.steps.length)} · {t("progress.seconds", { s: seconds(total) })}
      </button>
    );
  }

  const stageLabel =
    progress.stage === "insight"
      ? t("progress.insight")
      : progress.stage === "chart"
      ? t("progress.chart")
      : progress.stage === "dashboard"
      ? t("progress.dashboard")
      : null;

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
      {progress.planning && <div className="animate-pulse">{t("progress.planning")}</div>}
      {progress.steps.length > 0 && (
        <ul className="space-y-1">
          {progress.steps.map((step) => (
            <StepRow key={step.step_id} step={step} />
          ))}
        </ul>
      )}
      {!finished && stageLabel && <div className="mt-1 animate-pulse">{stageLabel}</div>}
    </div>
  );
}
