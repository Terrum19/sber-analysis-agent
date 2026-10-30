"use client";

/**
 * Прогресс хода по событиям progress: «Планирую…», затем список шагов
 * (purpose, статус, время). Когда ход закончен — свёрнут в строку
 * «N шагов · X с», по клику раскрывается. В режиме дашборда (#224) вместо
 * плоского списка — плитки плана, у каждой свой статус и свои шаги.
 * Остановленный или упавший ход с готовыми шагами — кнопка «Продолжить».
 */
import { useEffect, useState } from "react";
import Icon from "@/components/ui/Icon";
import { useT, type Translate } from "@/i18n/useT";
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

function TileRow({ tile, steps }: { tile: TileState; steps: StepState[] }) {
  const t = useT();
  const [open, setOpen] = useState(false);
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
      <span className="inline-block h-[13px] w-[13px] rounded-full border border-graphite-600" />
    );
  return (
    <li className="rounded-lg border border-graphite-800 bg-graphite-850/60 px-2.5 py-1.5">
      <button
        onClick={() => setOpen((v) => !v)}
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
          {steps.map((step) => (
            <StepRow key={step.step_id} step={step} />
          ))}
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
function ResumeBar({ onResume }: { onResume: () => void }) {
  const t = useT();
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-2">
      <span className="text-[12px] text-warn">{t("progress.interrupted")}</span>
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

export default function StepProgress({ progress, onResume }: { progress: TurnProgress; onResume?: () => void }) {
  const t = useT();
  const locale = useUiStore((s) => s.locale);
  const [expanded, setExpanded] = useState(false);
  const finished = progress.finished_at !== undefined;
  const now = useNow(!finished);
  const tiles = progress.tiles;

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
        {onResume && <ResumeBar onResume={onResume} />}
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
              steps={progress.steps.filter((step) => step.tile_id === tile.tile_id)}
            />
          ))}
        </ul>
      )}
      {!tiles && progress.steps.length > 0 && (
        <ul className="space-y-1">
          {progress.steps.map((step) => (
            <StepRow key={step.step_id} step={step} />
          ))}
        </ul>
      )}
      {!finished && stageLabel && <div className="mt-1 animate-pulse">{stageLabel}</div>}
      {quiet && <div className="mt-1 text-warn">{t("progress.stillWorking")}</div>}
      {finished && onResume && <ResumeBar onResume={onResume} />}
    </div>
  );
}
