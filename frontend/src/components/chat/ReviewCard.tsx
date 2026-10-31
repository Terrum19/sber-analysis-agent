"use client";

/**
 * Проверка ответа (режим «Проверка», A-29). Пока работает критик — «Проверяю
 * ответ…» с перебором пунктов чек-листа и счётчиком секунд; затем вердикт:
 * чек-лист (пункты с проблемой — первыми), проблемы с тем, что сделать, и
 * оговорки. Если ответ исправляется — строка «Дополняю исследование: …» и шаги
 * исправления; новые шаги помечены «добавлено проверкой». DraftAnswer —
 * черновой ответ, заменённый исправленным: свёрнут в «Ответ до проверки».
 */
import { useEffect, useMemo, useState } from "react";
import StepProgress from "@/components/chat/StepProgress";
import Icon from "@/components/ui/Icon";
import { useT, type Translate } from "@/i18n/useT";
import type { MessageKey } from "@/i18n/dictionary";
import type { ChatMessage } from "@/store/sessionStore";
import {
  finalReview,
  type ReviewCheck,
  type ReviewCheckId,
  type ReviewPayload,
  type ReviewState,
  type ReviewVerdict,
} from "@/types/chat";

const CHECK_ORDER: ReviewCheckId[] = [
  "answers_question",
  "metric",
  "period",
  "filters",
  "nulls",
  "outliers",
  "columns",
  "joins",
  "claims",
  "numbers",
];
/** Как часто подсвечивается следующий пункт чек-листа, пока ждём вердикт. */
const CYCLE_MS = 900;
const REPAIRS: ReviewVerdict[] = ["fix_insight", "extend_plan", "replan"];

function useTicker(active: boolean, ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(timer);
  }, [active, ms]);
  return now;
}

function checkTitle(t: Translate, id: ReviewCheckId, fallback?: string): string {
  const key = `verify.check.${id}` as MessageKey;
  const text = t(key);
  return text === key ? fallback || id : text;
}

function Spinner({ warn }: { warn?: boolean }) {
  return <Icon name="refresh" size={13} className={`shrink-0 animate-spin ${warn ? "text-warn" : "text-graphite-400"}`} />;
}

function VerdictBadge({ review, repaired }: { review: ReviewPayload; repaired: boolean }) {
  const t = useT();
  const repairing = REPAIRS.includes(review.action);
  const [label, tone] =
    review.verdict === "ok"
      ? [t(repaired ? "verify.okFixed" : "verify.ok"), "border-good/40 bg-good/10 text-good"]
      : repairing
      ? [t("verify.problem"), "border-orange-400/40 bg-orange-400/10 text-orange-300"]
      : [t("verify.caveat"), "border-warn/40 bg-warn/10 text-warn"];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11.5px] font-medium ${tone}`}>
      <Icon name={review.verdict === "ok" ? "check" : "alertTriangle"} size={11} />
      {label}
    </span>
  );
}

function CheckRow({ check }: { check: ReviewCheck }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const icon =
    check.status === "ok" ? (
      <Icon name="check" size={12} className="text-good" />
    ) : check.status === "issue" ? (
      <Icon name="alertTriangle" size={12} className="text-warn" />
    ) : (
      <span className="inline-block w-3 text-center text-graphite-600">—</span>
    );
  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        disabled={!check.evidence}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left hover:text-graphite-200 disabled:cursor-default"
      >
        <span className="shrink-0">{icon}</span>
        <span className={check.status === "issue" ? "text-graphite-100" : check.status === "n/a" ? "text-graphite-500" : ""}>
          {checkTitle(t, check.id, check.title)}
        </span>
        {check.evidence && (
          <Icon name={open ? "chevronDown" : "chevronRight"} size={10} className="text-graphite-600" />
        )}
      </button>
      {open && check.evidence && <div className="mb-1 ml-5 text-[11.5px] text-graphite-500">{check.evidence}</div>}
    </li>
  );
}

function ReviewDetails({ review, title }: { review: ReviewPayload; title: string }) {
  const t = useT();
  // Пункты с проблемой — первыми, дальше в порядке чек-листа
  const checks = useMemo(
    () =>
      [...review.checks].sort(
        (a, b) =>
          Number(b.status === "issue") - Number(a.status === "issue") ||
          CHECK_ORDER.indexOf(a.id) - CHECK_ORDER.indexOf(b.id),
      ),
    [review.checks],
  );
  const [open, setOpen] = useState(review.issues.length > 0);
  return (
    <div className="mt-1.5">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1 text-[12px] text-graphite-500 hover:text-graphite-300"
      >
        <Icon name={open ? "chevronDown" : "chevronRight"} size={12} />
        {title} · {t("verify.checked", { ok: review.checks.filter((c) => c.status === "ok").length, total: review.checks.length })}
      </button>
      {open && (
        <div className="mt-1 space-y-2 pl-4">
          {review.issues.length > 0 && (
            <ul className="space-y-1.5">
              {review.issues.map((issue, index) => (
                <li
                  key={index}
                  className={`rounded-md border px-2 py-1.5 ${
                    issue.severity === "material" ? "border-warn/40 bg-warn/10" : "border-graphite-800 bg-graphite-850/60"
                  }`}
                >
                  <div className="flex flex-wrap items-center gap-x-2 text-[10.5px] uppercase tracking-wide">
                    <span className="text-graphite-500">{checkTitle(t, issue.check)}</span>
                    <span className={issue.severity === "material" ? "text-warn" : "text-graphite-500"}>
                      {t(issue.severity === "material" ? "verify.material" : "verify.minor")}
                    </span>
                  </div>
                  <div className="text-graphite-100">{issue.message}</div>
                  {issue.evidence && <div className="mt-0.5 text-[11.5px] text-graphite-500">{issue.evidence}</div>}
                  {issue.fix && <div className="mt-0.5 text-[11.5px] text-graphite-300">{t("verify.fix", { fix: issue.fix })}</div>}
                </li>
              ))}
            </ul>
          )}
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-x-4 gap-y-0.5">
            {checks.map((check) => (
              <CheckRow key={check.id} check={check} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function ReviewCard({ review, sessionId }: { review: ReviewState; sessionId?: string }) {
  const t = useT();
  const active = review.finished_at === undefined;
  const now = useTicker(active, CYCLE_MS);
  const elapsed = Math.max(0, Math.floor((now - review.stage_started_at) / 1000));
  const final = finalReview(review);
  const first = review.reviews[0];
  const repaired = review.repair_status === "finished";
  const caveats = final?.caveats ?? [];
  // Пока ждём вердикт — по кругу подсвечиваем, что проверяется
  const cycling = CHECK_ORDER[Math.floor(now / CYCLE_MS) % CHECK_ORDER.length];
  const repairLabel =
    review.action === "replan" ? "verify.replan" : review.action === "fix_insight" ? "verify.fixInsight" : "verify.extend";
  const known = useMemo(() => new Set(review.known_steps ?? []), [review.known_steps]);

  return (
    <div className="mt-1 rounded-xl border border-graphite-800 bg-graphite-850/50 px-3 py-2 text-[12.5px] text-graphite-300">
      <div className="flex flex-wrap items-center gap-2">
        <Icon name="search" size={13} className="shrink-0 text-graphite-400" />
        <span className="font-medium text-graphite-200">{t("verify.title")}</span>
        {final && !active && <VerdictBadge review={final} repaired={repaired} />}
        {final && active && REPAIRS.includes(final.action) && <VerdictBadge review={final} repaired={false} />}
      </div>

      {active && review.status === "reviewing" && (
        <div className="mt-1.5">
          <div className="flex items-center gap-2">
            <Spinner />
            <span>{t(review.round > 1 ? "verify.reviewingAgain" : "verify.reviewing")}</span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-graphite-500">{t("progress.seconds", { s: elapsed })}</span>
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {CHECK_ORDER.map((id) => (
              <span
                key={id}
                className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors ${
                  id === cycling ? "border-graphite-400 bg-graphite-700 text-graphite-50" : "border-graphite-800 text-graphite-500"
                }`}
              >
                {checkTitle(t, id)}
              </span>
            ))}
          </div>
        </div>
      )}

      {first && <ReviewDetails review={first} title={t("verify.round1")} />}

      {(review.action && REPAIRS.includes(review.action) && review.repair_status) && (
        <div className="mt-2 border-t border-graphite-800 pt-1.5">
          <div className="flex items-center gap-2">
            {review.repair_status === "started" ? (
              <Spinner warn />
            ) : review.repair_status === "failed" ? (
              <Icon name="x" size={13} className="shrink-0 text-bad" />
            ) : (
              <Icon name="check" size={13} className="shrink-0 text-good" />
            )}
            <span className={review.repair_status === "failed" ? "text-bad" : "text-graphite-200"}>
              {review.repair_status === "failed"
                ? t("verify.repairFailed")
                : t(repairLabel, { reason: review.reason || "" })}
            </span>
            {review.repair_status === "started" && (
              <span className="shrink-0 whitespace-nowrap tabular-nums text-graphite-500">{t("progress.seconds", { s: elapsed })}</span>
            )}
          </div>
          {review.repair && review.repair.steps.length > 0 && (
            <div className="mt-1 pl-5">
              <StepProgress progress={review.repair} sessionId={sessionId} knownSteps={known} />
            </div>
          )}
          {review.repair_status === "started" && review.repair?.stage && (
            <div className="mt-1 pl-5 animate-pulse text-graphite-500">
              {t(review.repair.stage === "chart" ? "progress.chart" : "progress.insight")}
            </div>
          )}
        </div>
      )}

      {final && final !== first && <ReviewDetails review={final} title={t("verify.round2")} />}

      {caveats.length > 0 && (
        <div className="mt-2 rounded-md border border-warn/30 bg-warn/5 px-2 py-1.5">
          <div className="mb-0.5 text-[11px] uppercase tracking-wide text-warn">{t("verify.caveats")}</div>
          <ul className="space-y-0.5 text-graphite-200">
            {caveats.map((text, index) => (
              <li key={index}>• {text}</li>
            ))}
          </ul>
        </div>
      )}

      {review.status === "failed" && (
        <div className="mt-1.5 text-warn" title={review.error}>
          {t("verify.failed")}
        </div>
      )}
    </div>
  );
}

/** Черновой ответ, заменённый исправленным: свёрнут, по клику — каким он был. */
export function DraftAnswer({ message }: { message: ChatMessage }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <div className="mx-auto w-full max-w-3xl">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        title={t("verify.draftHint")}
        className="inline-flex items-center gap-1 text-[12px] text-graphite-500 hover:text-graphite-300"
      >
        <Icon name={open ? "chevronDown" : "chevronRight"} size={12} />
        {t(message.chart ? "verify.draftChart" : "verify.draft")}
      </button>
      {open && (
        <div className="mt-1 whitespace-pre-wrap border-l-2 border-graphite-700 pl-3 text-sm text-graphite-500 line-through decoration-graphite-600/60">
          {message.chart ? message.chart.caption || message.content : message.content}
        </div>
      )}
    </div>
  );
}
