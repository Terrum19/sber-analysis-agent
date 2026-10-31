"use client";

/**
 * Варианты дашборда над строкой ввода (#224): пока идеи генерируются —
 * 4 скелетона и счётчик секунд, потом 4 карточки и «Свой вариант».
 * Ошибка — текст, «Повторить» и «Свой вариант».
 */
import { useEffect, useState } from "react";
import Icon from "@/components/ui/Icon";
import { useT } from "@/i18n/useT";
import type { DashboardIdea } from "@/types/chat";

export type IdeasState =
  | { status: "loading"; started_at: number }
  | { status: "ready"; ideas: DashboardIdea[] }
  | { status: "error"; message: string; noModel: boolean };

interface Props {
  state: IdeasState;
  disabled: boolean;
  onPick: (idea: DashboardIdea) => void;
  onOwn: () => void;
  onRetry: () => void;
  onClose: () => void;
}

export function Elapsed({ since }: { since: number }) {
  const t = useT();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return <span className="tabular-nums text-graphite-500">{t("progress.seconds", { s: Math.floor((now - since) / 1000) })}</span>;
}

export default function DashboardIdeas({ state, disabled, onPick, onOwn, onRetry, onClose }: Props) {
  const t = useT();
  const ownButton = (
    <button
      onClick={onOwn}
      className="flex items-center justify-center gap-1.5 rounded-xl border border-dashed sm:col-span-2 border-graphite-600 px-3 py-2 text-[12.5px] text-graphite-300 hover:bg-graphite-800 hover:text-graphite-50 transition"
    >
      <Icon name="pencil" size={13} />
      {t("dashboard.ownVariant")}
    </button>
  );

  return (
    <div className="mb-3 rounded-2xl border border-graphite-700 bg-graphite-900/80 p-3">
      <div className="mb-2 flex items-center justify-between gap-2 text-[12px] text-graphite-400">
        <span className="flex items-center gap-1.5">
          <Icon name="sparkle" size={13} />
          {state.status === "loading" ? (
            <>
              <span className="animate-pulse">{t("dashboard.ideasLoading")}</span>
              <Elapsed since={state.started_at} />
            </>
          ) : (
            t("dashboard.autoGenerate")
          )}
        </span>
        <button onClick={onClose} className="rounded-md px-1.5 py-0.5 text-graphite-500 hover:bg-graphite-800 hover:text-graphite-200">
          {t("dashboard.hideIdeas")}
        </button>
      </div>

      {state.status === "error" ? (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-bad/30 bg-bad/10 px-3 py-2 text-xs text-bad">
          <span className="min-w-0 flex-1">
            {state.noModel ? t("dashboard.ideasNoModel") : t("dashboard.ideasError", { msg: state.message })}
          </span>
          <button
            onClick={onRetry}
            className="inline-flex items-center gap-1.5 rounded-lg bg-danger hover:bg-danger/90 px-3 py-1.5 text-xs font-semibold text-white transition"
          >
            <Icon name="refresh" size={13} />
            {t("chat.retry")}
          </button>
          <button
            onClick={onOwn}
            className="rounded-lg border border-graphite-600 px-3 py-1.5 text-xs text-graphite-200 hover:bg-graphite-800 transition"
          >
            {t("dashboard.ownVariant")}
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {state.status === "loading"
            ? [0, 1, 2, 3].map((i) => (
                <div key={i} className="min-h-[72px] animate-pulse rounded-xl border border-graphite-800 bg-graphite-850 p-3">
                  <div className="h-3 w-3/4 rounded bg-graphite-700" />
                  <div className="mt-2 h-2.5 w-full rounded bg-graphite-800" />
                  <div className="mt-1.5 h-2.5 w-2/3 rounded bg-graphite-800" />
                </div>
              ))
            : state.ideas.map((idea) => (
                <button
                  key={idea.id || idea.title}
                  onClick={() => onPick(idea)}
                  disabled={disabled}
                  className="min-h-[72px] rounded-xl border border-graphite-700 bg-graphite-850 p-3 text-left hover:border-graphite-500 hover:bg-graphite-800 disabled:opacity-50 disabled:cursor-not-allowed transition"
                >
                  <span className="block text-[13px] font-medium text-graphite-50">{idea.title}</span>
                  {idea.description && (
                    <span className="mt-1 block text-[11.5px] leading-snug text-graphite-400 line-clamp-3">{idea.description}</span>
                  )}
                </button>
              ))}
          {ownButton}
        </div>
      )}
    </div>
  );
}
