"use client";

/**
 * Статус хода агента у сессии: жёлтая точка — агент отвечает, синяя — ответ готов,
 * но его ещё не открывали, красная — ход сорвался. Без хода точки нет.
 */
import type { ChatRun } from "@/store/chatRunStore";
import { useT } from "@/i18n/useT";

const DOT_CLASS = {
  running: "bg-warn animate-pulse",
  done: "bg-blue-500",
  error: "bg-bad",
} as const;

const DOT_LABEL = {
  running: "sidebar.runRunning",
  done: "sidebar.runDone",
  error: "sidebar.runError",
} as const;

export default function RunDot({ run }: { run: ChatRun | undefined }) {
  const t = useT();
  const kind = run?.status === "running" ? "running" : run?.unseen ? run.status : null;
  if (!kind) return null;
  const label = t(DOT_LABEL[kind]);
  return <span role="status" aria-label={label} title={label} className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT_CLASS[kind]}`} />;
}
