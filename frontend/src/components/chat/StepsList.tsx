"use client";

/**
 * Шаги хода автора из импортированной сессии (#250): инструмент, цель и
 * аргументы. Получатель видит, что и как посчитано, даже без пересчёта.
 */
import type { ImportedStep } from "@/store/sessionStore";
import { useT } from "@/i18n/useT";

function argumentsText(step: ImportedStep): string {
  const args = { ...(step.arguments ?? {}), ...(step.columns?.length ? { columns: step.columns } : {}) };
  return Object.keys(args).length ? JSON.stringify(args, null, 1).replace(/\n\s*/g, " ") : "";
}

export default function StepsList({ steps }: { steps: ImportedStep[] }) {
  const t = useT();
  return (
    <details className="mt-2 max-w-2xl rounded-lg border border-graphite-800 bg-graphite-900/60 px-3 py-1.5 text-[12px]">
      <summary className="cursor-pointer select-none text-graphite-400 hover:text-graphite-200">
        {t("chat.steps", { n: steps.length })}
      </summary>
      <ol className="mt-1.5 space-y-1.5 pb-1">
        {steps.map((step, i) => (
          <li key={`${step.step_id ?? i}`} className="space-y-0.5">
            <div className="text-graphite-200">
              <span className="font-mono text-graphite-400">{step.tool ?? "—"}</span>
              {step.purpose ? ` — ${step.purpose}` : ""}
            </div>
            {argumentsText(step) && (
              <div className="break-all font-mono text-[11px] text-graphite-500">{argumentsText(step)}</div>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}
