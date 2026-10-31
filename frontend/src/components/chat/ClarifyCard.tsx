"use client";

/**
 * Карточка уточняющих вопросов глубокого исследования (A-28), по образцу
 * DashboardIdeas: пока вопросы готовятся — «Продумываю вопросы…» и счётчик
 * секунд, потом все вопросы сразу. У вопроса — варианты (один или несколько
 * по multi), «Свой ответ» и «На усмотрение агента» (выбран по умолчанию; такой
 * ответ в ход не уходит). «Начать исследование» отправляет ответы,
 * «Пропустить» — ход без них. Ошибка — «Повторить» и «Исследовать без уточнений».
 * После отправки карточка сворачивается в строку уточнений.
 */
import { useState } from "react";
import Icon from "@/components/ui/Icon";
import { Elapsed } from "@/components/chat/DashboardIdeas";
import { useT } from "@/i18n/useT";
import type { Clarification, ClarifyCardData, ClarifyQuestion } from "@/types/chat";

interface Props {
  data: ClarifyCardData;
  /** Неактивная карточка (задан новый вопрос или идёт ход) — без кнопок. */
  active: boolean;
  onStart: (clarifications: Clarification[]) => void;
  onRetry: () => void;
}

interface Choice {
  picked: string[];
  own: string;
  ownOpen: boolean;
}

const EMPTY: Choice = { picked: [], own: "", ownOpen: false };

const chip = (selected: boolean) =>
  `rounded-lg border px-2.5 py-1 text-xs transition disabled:opacity-50 disabled:cursor-not-allowed ${
    selected
      ? "border-graphite-400 bg-graphite-700 text-graphite-50"
      : "border-graphite-700 text-graphite-300 hover:bg-graphite-800 hover:text-graphite-100"
  }`;

/** Ответ на вопрос: выбранные варианты и свой текст; пусто — на усмотрение агента. */
function answerOf(choice: Choice): string {
  return [...choice.picked, choice.own.trim()].filter(Boolean).join(", ");
}

export default function ClarifyCard({ data, active, onStart, onRetry }: Props) {
  const t = useT();
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const state = data.state;

  if (state.status === "answered") {
    const list = state.clarifications
      .map((item) => `${item.axis ? t(`research.axis.${item.axis}`) : item.question} — ${item.answer}`)
      .join(", ");
    return (
      <div className="flex items-start gap-1.5 text-[12px] text-graphite-400">
        <Icon name="search" size={12} className="mt-0.5 shrink-0" />
        <span>{list ? t("research.summary", { list }) : t("research.skipped")}</span>
      </div>
    );
  }

  if (state.status === "loading") {
    return (
      <div className="flex items-center gap-1.5 text-[12.5px] text-graphite-400">
        <Icon name="sparkle" size={13} />
        <span className="animate-pulse">{t("research.loading")}</span>
        <Elapsed since={state.started_at} />
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="max-w-xl rounded-xl border border-bad/30 bg-bad/10 px-3.5 py-3 text-xs text-bad">
        <div>{state.message ? t("research.errorDetail", { msg: state.message }) : t("research.error")}</div>
        {active && (
          <div className="mt-2.5 flex flex-wrap gap-2">
            <button
              onClick={onRetry}
              className="inline-flex items-center gap-1.5 rounded-lg bg-danger hover:bg-danger/90 px-3 py-1.5 text-xs font-semibold text-white transition"
            >
              <Icon name="refresh" size={13} />
              {t("chat.retry")}
            </button>
            <button
              onClick={() => onStart([])}
              className="rounded-lg border border-graphite-600 px-3 py-1.5 text-xs text-graphite-200 hover:bg-graphite-800 transition"
            >
              {t("research.withoutClarify")}
            </button>
          </div>
        )}
      </div>
    );
  }

  const choiceOf = (question: ClarifyQuestion) => choices[question.id] ?? EMPTY;
  const update = (question: ClarifyQuestion, change: (choice: Choice) => Choice) =>
    setChoices((prev) => ({ ...prev, [question.id]: change(prev[question.id] ?? EMPTY) }));

  function toggle(question: ClarifyQuestion, option: string) {
    update(question, (choice) => {
      if (choice.picked.includes(option)) {
        return { ...choice, picked: choice.picked.filter((item) => item !== option) };
      }
      // Один вариант: выбор заменяет прежний и свой ответ
      return question.multi
        ? { ...choice, picked: [...choice.picked, option] }
        : { picked: [option], own: "", ownOpen: false };
    });
  }

  function start() {
    onStart(
      state.status === "ready"
        ? state.questions
            .map((question) => ({ question: question.text, answer: answerOf(choiceOf(question)), axis: question.axis }))
            .filter((item) => item.answer)
        : [],
    );
  }

  return (
    <div className="max-w-xl rounded-xl border border-graphite-700 bg-graphite-900/80 px-3.5 py-3 text-[13px]">
      <div className="mb-2.5 flex items-center gap-1.5 font-medium text-graphite-100">
        <Icon name="search" size={14} />
        {t("research.title")}
      </div>
      <div className="flex flex-col gap-3">
        {state.questions.map((question) => {
          const choice = choiceOf(question);
          const byAgent = !answerOf(choice) && !choice.ownOpen;
          return (
            <div key={question.id}>
              <div className="mb-1.5 text-graphite-200">
                {question.text}
                {question.multi && <span className="ml-1.5 text-[11px] text-graphite-500">{t("research.multi")}</span>}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {question.options.map((option) => (
                  <button
                    key={option}
                    onClick={() => toggle(question, option)}
                    disabled={!active}
                    aria-pressed={choice.picked.includes(option)}
                    className={chip(choice.picked.includes(option))}
                  >
                    {option}
                  </button>
                ))}
                <button
                  onClick={() =>
                    update(question, (prev) =>
                      question.multi ? { ...prev, ownOpen: !prev.ownOpen } : { picked: [], own: prev.own, ownOpen: !prev.ownOpen },
                    )
                  }
                  disabled={!active}
                  aria-pressed={choice.ownOpen}
                  className={`${chip(choice.ownOpen)} inline-flex items-center gap-1`}
                >
                  <Icon name="pencil" size={11} />
                  {t("research.own")}
                </button>
                <button
                  onClick={() => update(question, () => EMPTY)}
                  disabled={!active}
                  aria-pressed={byAgent}
                  className={chip(byAgent)}
                >
                  {t("research.agentChoice")}
                </button>
              </div>
              {choice.ownOpen && (
                <input
                  autoFocus
                  value={choice.own}
                  onChange={(e) => update(question, (prev) => ({ ...prev, own: e.target.value }))}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") start();
                  }}
                  disabled={!active}
                  placeholder={t("research.ownPlaceholder")}
                  className="mt-1.5 w-full rounded-lg border border-graphite-700 bg-graphite-850 px-2.5 py-1.5 text-xs text-graphite-100 placeholder-graphite-500 outline-none focus:border-graphite-500"
                />
              )}
            </div>
          );
        })}
      </div>
      {active && (
        <div className="mt-3.5 flex flex-wrap gap-2">
          <button
            onClick={start}
            className="inline-flex items-center gap-1.5 rounded-lg bg-graphite-100 px-3 py-1.5 text-xs font-semibold text-graphite-950 hover:bg-graphite-50 transition"
          >
            <Icon name="arrowUp" size={13} />
            {t("research.start")}
          </button>
          <button
            onClick={() => onStart([])}
            className="rounded-lg border border-graphite-600 px-3 py-1.5 text-xs text-graphite-200 hover:bg-graphite-800 transition"
          >
            {t("research.skip")}
          </button>
        </div>
      )}
    </div>
  );
}
