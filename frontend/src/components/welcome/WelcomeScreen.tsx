"use client";

/**
 * Экран приветствия (по образцу Welcome в VS Code):
 * - Начало: загрузка CSV, подключение БД, реестр дашбордов
 * - Недавние базы данных: открыть новую сессию в базе
 * - Что умеет приложение и пошаговый туториал
 */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { useUiStore } from "@/store/uiStore";
import { useStartSession } from "@/hooks/useStartSession";
import { sessionsLabel, useT } from "@/i18n/useT";
import type { MessageKey } from "@/i18n/dictionary";
import Icon, { type IconName } from "@/components/ui/Icon";

const CAPABILITIES: MessageKey[] = ["welcome.cap1", "welcome.cap2", "welcome.cap3", "welcome.cap4"];

const TUTORIAL_STEPS: Array<[MessageKey, MessageKey]> = [
  ["welcome.step1", "welcome.step1Text"],
  ["welcome.step2", "welcome.step2Text"],
  ["welcome.step3", "welcome.step3Text"],
  ["welcome.step4", "welcome.step4Text"],
  ["welcome.step5", "welcome.step5Text"],
];

function StartLink({ icon, label, onClick }: { icon: IconName; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-2.5 py-1 text-[13px] text-graphite-200 hover:text-graphite-50 transition"
    >
      <Icon name={icon} className="text-graphite-400" />
      <span className="hover:underline underline-offset-4 decoration-graphite-500">{label}</span>
    </button>
  );
}

export default function WelcomeScreen() {
  const t = useT();
  const router = useRouter();
  const locale = useUiStore((s) => s.locale);
  const databases = useSessionStore((s) => s.databases);
  const requestCsvPick = useUiStore((s) => s.requestCsvPick);
  const setDbModalOpen = useUiStore((s) => s.setDbModalOpen);
  const startSession = useStartSession();
  const [tutorialOpen, setTutorialOpen] = useState(false);
  const [activeStep, setActiveStep] = useState(0);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-8 py-14 md:py-20">
        <h1 className="text-4xl font-light tracking-tight text-graphite-50 md:text-5xl">{t("welcome.title")}</h1>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-graphite-400">{t("welcome.intro")}</p>

        <div className="mt-12 grid gap-12 md:grid-cols-2">
          {/* Левая колонка */}
          <div className="space-y-10">
            <section>
              <h2 className="mb-3 text-[15px] font-medium text-graphite-100">{t("welcome.start")}</h2>
              <div className="flex flex-col items-start gap-0.5">
                <button
                  type="button"
                  disabled={databases.length === 0}
                  onClick={() => databases.length > 0 && startSession(databases[0])}
                  className="flex items-center gap-2.5 py-1 text-[13px] text-graphite-200 hover:text-graphite-50 transition disabled:opacity-40 disabled:cursor-not-allowed text-left"
                  title={databases.length === 0 ? t("welcome.noDataToStart") : t("sidebar.newSession")}
                >
                  <Icon name="plus" className="text-graphite-400 shrink-0" />
                  <span className={databases.length > 0 ? "hover:underline underline-offset-4 decoration-graphite-500" : ""}>
                    {t("sidebar.newSession")}
                  </span>
                  {databases.length === 0 && (
                    <span className="text-[11px] text-graphite-500 font-normal">
                      ({t("welcome.noDataToStart")})
                    </span>
                  )}
                </button>
                <StartLink icon="upload" label={t("welcome.uploadCsv")} onClick={requestCsvPick} />
                <StartLink icon="database" label={t("welcome.connectDb")} onClick={() => setDbModalOpen(true)} />
                <StartLink icon="layout" label={t("welcome.openRegistry")} onClick={() => router.push("/dashboards")} />
              </div>
            </section>

            <section>
              <h2 className="mb-3 text-[15px] font-medium text-graphite-100">{t("welcome.recent")}</h2>
              {databases.length === 0 ? (
                <p className="text-[13px] text-graphite-500">{t("welcome.noRecent")}</p>
              ) : (
                <div className="flex flex-col items-start">
                  {databases.slice(0, 5).map((d) => (
                    <button
                      key={d.id}
                      type="button"
                      onClick={() => startSession(d)}
                      className="flex max-w-full items-center gap-2.5 py-1 text-[13px] text-graphite-200 hover:text-graphite-50"
                      title={t("welcome.openSession")}
                    >
                      <Icon name="folder" className="shrink-0 text-graphite-400" />
                      <span className="truncate hover:underline underline-offset-4 decoration-graphite-500">{d.title}</span>
                      <span className="shrink-0 text-[12px] text-graphite-500">{sessionsLabel(locale, d.sessions.length)}</span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </div>

          {/* Правая колонка */}
          <div className="space-y-10">
            <section>
              <h2 className="mb-3 text-[15px] font-medium text-graphite-100">{t("welcome.whatCanDo")}</h2>
              <ul className="space-y-2.5">
                {CAPABILITIES.map((key) => (
                  <li key={key} className="flex gap-2.5 text-[13px] leading-relaxed text-graphite-300">
                    <Icon name="check" className="mt-0.5 shrink-0 text-graphite-500" />
                    <span>{t(key)}</span>
                  </li>
                ))}
              </ul>
            </section>

            <section>
              <h2 className="mb-3 text-[15px] font-medium text-graphite-100">{t("welcome.learn")}</h2>
              <div className="overflow-hidden rounded-xl border border-graphite-800 bg-graphite-950/60">
                <button
                  type="button"
                  onClick={() => setTutorialOpen((v) => !v)}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-graphite-850 transition"
                >
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-graphite-800 text-graphite-200">
                    <Icon name="book" />
                  </span>
                  <span className="flex-1">
                    <span className="block text-[14px] font-medium text-graphite-100">{t("welcome.tutorial")}</span>
                    <span className="block text-[12px] text-graphite-500">{t("welcome.tutorialMeta")}</span>
                  </span>
                  <Icon name={tutorialOpen ? "chevronDown" : "chevronRight"} className="text-graphite-500" />
                </button>

                {tutorialOpen && (
                  <ol className="border-t border-graphite-800 px-2 py-2">
                    {TUTORIAL_STEPS.map(([titleKey, textKey], i) => {
                      const active = i === activeStep;
                      return (
                        <li key={titleKey}>
                          <button
                            type="button"
                            onClick={() => setActiveStep(i)}
                            className={`flex w-full gap-3 rounded-lg px-2 py-2 text-left transition ${
                              active ? "bg-graphite-800/70" : "hover:bg-graphite-850"
                            }`}
                          >
                            <span
                              className={`mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] ${
                                i < activeStep
                                  ? "border-graphite-400 bg-graphite-400 text-graphite-950"
                                  : active
                                  ? "border-graphite-200 text-graphite-100"
                                  : "border-graphite-600 text-graphite-500"
                              }`}
                            >
                              {i < activeStep ? <Icon name="check" size={12} /> : i + 1}
                            </span>
                            <span className="min-w-0">
                              <span className={`block text-[13px] ${active ? "text-graphite-50" : "text-graphite-300"}`}>
                                {t(titleKey)}
                              </span>
                              {active && <span className="mt-1 block text-[12px] leading-relaxed text-graphite-400">{t(textKey)}</span>}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                    <li className="flex justify-end gap-2 px-2 pt-2">
                      {activeStep > 0 && (
                        <button
                          type="button"
                          onClick={() => setActiveStep((s) => s - 1)}
                          className="rounded-lg px-3 py-1.5 text-[12px] text-graphite-400 hover:text-graphite-100"
                        >
                          {t("welcome.back")}
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() =>
                          activeStep < TUTORIAL_STEPS.length - 1 ? setActiveStep((s) => s + 1) : setTutorialOpen(false)
                        }
                        className="rounded-lg bg-graphite-100 px-3 py-1.5 text-[12px] font-medium text-graphite-950 hover:bg-graphite-50"
                      >
                        {activeStep < TUTORIAL_STEPS.length - 1 ? t("welcome.next") : t("welcome.done")}
                      </button>
                    </li>
                  </ol>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
