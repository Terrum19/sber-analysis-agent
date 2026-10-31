"use client";

/**
 * Ходы агента вне React: стрим принадлежит сессии, а не открытому чату, поэтому
 * переход на дашборд, в другой чат или вкладку его не обрывает — ответ дописывается
 * в ленту своей сессии. Статус хода — точка у сессии в сайдбаре: жёлтая — агент
 * отвечает, синяя — ответ готов, но его ещё не открывали, красная — ход сорвался.
 */
import { create } from "zustand";
import { streamChat, type DashboardRequest } from "@/api/client";
import { sessionSnapshot, useSessionStore } from "@/store/sessionStore";
import { useCanvasStore } from "@/store/canvasStore";
import { useUiStore } from "@/store/uiStore";
import { translate } from "@/i18n/useT";
import type { MessageKey } from "@/i18n/dictionary";
import type { DashboardTile } from "@/types/dashboard";
import {
  applyOutline,
  applyProgress,
  hasFinishedWork,
  type Clarification,
  type DashboardEvent,
  type TurnProgress,
} from "@/types/chat";

export interface ChatRun {
  status: "running" | "done" | "error";
  /** Ход закончился, когда сессия была закрыта: точка горит, пока её не откроют. */
  unseen: boolean;
  /** Исходный вопрос хода: его повторяют после сбоя ответа на уточнение. */
  question: string;
  /** Вопрос для баннера повтора; null — ход прошёл без сбоя. */
  failedQuestion: string | null;
  /** Режим дашборда упавшего хода: повтор из баннера идёт с ним же. */
  failedDashboard?: DashboardRequest;
  /** Уточнения упавшего хода (A-28): повтор идёт с ними же, без новых вопросов. */
  failedClarifications?: Clarification[];
  /** Карточка уточнения, на которую ещё можно ответить. */
  clarificationId: string | null;
}

interface ChatRunState {
  runs: Record<string, ChatRun | undefined>;
  patchRun: (sessionId: string, patch: Partial<ChatRun>) => void;
  /** Пользователь открыл сессию: точка «готово» / «ошибка» гаснет. */
  markSeen: (sessionId: string) => void;
}

export const useChatRunStore = create<ChatRunState>()((set) => ({
  runs: {},
  patchRun: (sessionId, patch) =>
    set((s) => {
      const prev = s.runs[sessionId] ?? {
        status: "done" as const,
        unseen: false,
        question: "",
        failedQuestion: null,
        clarificationId: null,
      };
      return { runs: { ...s.runs, [sessionId]: { ...prev, ...patch } } };
    }),
  markSeen: (sessionId) =>
    set((s) => {
      const run = s.runs[sessionId];
      if (!run?.unseen) return {};
      return { runs: { ...s.runs, [sessionId]: { ...run, unseen: false } } };
    }),
}));

/** Отмена стримов по сессиям. */
const stoppers = new Map<string, () => void>();

/** Плитка панели из события «chart»; id хранится в самом событии. */
export function chartTile(chartData: any, fallbackTitle: string): DashboardTile {
  return {
    id: chartData.tile_id,
    title: chartData.caption || fallbackTitle,
    spec: chartData.spec,
    result_table: chartData.result_table,
    insight: chartData.insight,
    // Новый график встаёт под существующими: сетка сама поднимет его вверх
    layout: { x: 0, y: 1000, w: 12, h: 5 },
  };
}

function t(key: MessageKey, vars?: Record<string, string | number>): string {
  return translate(useUiStore.getState().locale, key, vars);
}

/** Сессия сейчас на экране: её ответ уже виден, точка не нужна. */
function isViewing(sessionId: string): boolean {
  return typeof window !== "undefined" && window.location.pathname.endsWith(`/session/${sessionId}`);
}

export function isRunning(sessionId: string): boolean {
  return useChatRunStore.getState().runs[sessionId]?.status === "running";
}

/** Остановить ход сессии (кнопка «Стоп»). */
export function stopRun(sessionId: string) {
  stoppers.get(sessionId)?.();
}

/** Сессии удалены: их ходы останавливаем и забываем. */
export function forgetRuns(sessionIds: string[]) {
  for (const id of sessionIds) stopRun(id);
  useChatRunStore.setState((s) => {
    const runs = { ...s.runs };
    for (const id of sessionIds) delete runs[id];
    return { runs };
  });
}

/**
 * Запустить ход агента. Вопрос пользователя уже в ленте; всё, что придёт
 * по стриму, пишется в ленту и плитки сессии sessionId.
 */
export function startRun(
  sessionId: string,
  q: string,
  options: {
    retryQuestion: string;
    recoveryResponse?: string;
    replyTo: string | null;
    dashboard?: DashboardRequest;
    /** «Продолжить» остановленный ход: turn_id из его прогресса, вопрос пользователя уже в ленте. */
    resumeTurn?: string;
    /** Ответы на уточняющие вопросы глубокого исследования (A-28). */
    clarifications?: Clarification[];
    /** Дашборд хода собран: открыть его вкладку, если пользователь всё ещё в этой сессии. */
    onDashboardBuilt?: (dashboardId: string) => void;
  },
) {
  if (isRunning(sessionId)) return;
  const store = useSessionStore.getState();
  const { retryQuestion, recoveryResponse, dashboard, clarifications } = options;
  const runs = useChatRunStore.getState();
  runs.patchRun(sessionId, {
    status: "running",
    unseen: false,
    failedQuestion: null,
    failedDashboard: undefined,
    failedClarifications: undefined,
    clarificationId: null,
    ...(recoveryResponse ? {} : { question: q }),
  });
  const addMessage = (msg: Parameters<typeof store.addMessage>[0]) => store.addMessage(msg, sessionId);
  const viewing = () => useSessionStore.getState().sessionId === sessionId;

  let answerStarted = false;
  let streamHadError = false;
  // Поток без done и без уточнения — оборван, это ошибка
  let streamFinished = false;
  let cancelled = false;
  // Прогресс хода живёт в отдельном сообщении над ответом
  let progress: TurnProgress | null = null;
  const progressId = `msg-progress-${Date.now().toString(36)}`;
  // interrupted — ход остановлен, упал или оборвался: если часть готова, его можно продолжить
  const finishProgress = (interrupted = false) => {
    if (progress && progress.finished_at === undefined) {
      const resumable = interrupted && Boolean(progress.turn_id) && hasFinishedWork(progress);
      progress = { ...progress, finished_at: Date.now(), ...(resumable ? { resumable } : {}) };
      store.updateMessage(progressId, { progress }, sessionId);
    }
  };
  const appendText = (text: string) => {
    if (answerStarted) {
      store.appendToLastMessage(text, sessionId);
    } else {
      answerStarted = true;
      addMessage({ role: "agent", content: text });
    }
  };
  // Дашборд хода в режиме дашборда: после done открываем его вкладку
  let builtDashboardId: string | null = null;
  const failDashboard = {
    ...(dashboard ? { failedDashboard: dashboard } : {}),
    ...(clarifications ? { failedClarifications: clarifications } : {}),
  };
  const fail = () => {
    streamHadError = true;
    runs.patchRun(sessionId, {
      failedQuestion: retryQuestion,
      failedDashboard: dashboard,
      failedClarifications: clarifications,
    });
  };

  // Что показали пользователю: нужно для PDF/Excel ответа (F-10).
  let answerHeadline = "";
  let answerDetails: string[] = [];
  let answerSpec: Record<string, unknown> | undefined;
  let answerResultTable: string | undefined;
  // turnId приходит в done: по нему работают «Спросить об этом» и reply_to (F-12).
  const saveAnswerMeta = (turnId?: string) => {
    const hasAnswer =
      Boolean(answerHeadline) || answerDetails.length > 0 || Boolean(answerSpec) || Boolean(answerResultTable);
    if (!hasAnswer && !turnId) {
      return;
    }
    const all = sessionSnapshot(useSessionStore.getState(), sessionId).messages;
    const last = all[all.length - 1];
    if (!last || last.role !== "agent" || last.failed || last.clarification || last.refusal) {
      return;
    }
    store.updateLastMessage(
      {
        ...(hasAnswer
          ? {
              answer: {
                headline: answerHeadline || undefined,
                details: answerDetails.length > 0 ? answerDetails : undefined,
                spec: answerSpec,
                result_table: answerResultTable,
              },
            }
          : {}),
        ...(turnId ? { turn_id: turnId } : {}),
      },
      sessionId,
    );
  };

  const stop = streamChat(
    sessionId,
    q,
    (event) => {
      if (event.type === "done") {
        streamFinished = true;
        finishProgress(streamHadError);
        saveAnswerMeta(event.data?.turn_id);
        if (builtDashboardId && isViewing(sessionId)) options.onDashboardBuilt?.(builtDashboardId);
      } else if (event.type === "dashboard_outline") {
        if (progress) {
          progress = applyOutline(progress, event.data);
          store.updateMessage(progressId, { progress }, sessionId);
        } else {
          progress = applyOutline({ planning: false, steps: [], started_at: Date.now() }, event.data);
          answerStarted = false;
          addMessage({ id: progressId, role: "agent", content: "", progress });
        }
      } else if (event.type === "dashboard_fallback") {
        answerStarted = false;
        // Дашборд строится по первой идее: в следующий раз её не предлагаем
        store.markIdeaUsed(sessionId, event.data.idea.title);
        addMessage({ role: "agent", content: event.data.message, dashboardFallback: event.data });
        if (progress) {
          // Баннер встал под прогрессом: переносим прогресс ниже, чтобы он оставался последним
          store.removeMessage(progressId, sessionId);
          addMessage({ id: progressId, role: "agent", content: "", progress });
        }
      } else if (event.type === "table") {
        // Ход идёт дальше в той же ленте, поэтому не switchSessionTable: та завела бы ленту новой таблицы
        store.followAgentTable(sessionId, event.data.table);
        addMessage({ role: "agent", content: t("chat.tableChosen", { name: event.data.title || event.data.table }) });
      } else if (event.type === "progress") {
        if (!progress) {
          progress = applyProgress({ planning: false, steps: [], started_at: Date.now() }, event.data);
          answerStarted = false;
          addMessage({ id: progressId, role: "agent", content: "", progress });
        } else {
          progress = applyProgress(progress, event.data);
          store.updateMessage(progressId, { progress }, sessionId);
        }
      } else if (event.type === "clarification_needed") {
        // Бэкенд закрывает поток без done: ждёт recovery_response
        answerStarted = false;
        streamFinished = true;
        const id = `msg-clarify-${Date.now().toString(36)}`;
        addMessage({ id, role: "agent", content: "", clarification: event.data });
        runs.patchRun(sessionId, { clarificationId: id });
      } else if (event.type === "refusal") {
        answerStarted = false;
        streamFinished = true;
        addMessage({ role: "agent", content: event.data.reason || t("chat.refusal"), refusal: event.data });
      } else if (event.type === "text") {
        const raw = event as any;
        const headline = event.data?.headline || raw.headline;
        if (typeof headline === "string" && headline) answerHeadline = headline;
        const content = event.data?.headline || event.data?.chunk || raw.headline || raw.chunk || "";
        if (content) appendText(content);
        const details = event.data?.details || raw.details;
        if (details && Array.isArray(details)) {
          answerDetails = details.filter((d: unknown): d is string => typeof d === "string");
          if (answerDetails.length > 0) {
            appendText("\n\n" + answerDetails.map((d: string) => `• ${d}`).join("\n"));
          }
        }
      } else if (event.type === "chart") {
        answerStarted = false;
        const chartData = { ...((event.data || (event as any)) as any), tile_id: `chart-${Date.now().toString(36)}` };
        if (typeof chartData.caption === "string" && chartData.caption) {
          answerHeadline = chartData.caption;
        }
        if (chartData.spec && typeof chartData.spec === "object") {
          answerSpec = chartData.spec as Record<string, unknown>;
        }
        if (typeof chartData.result_table === "string") {
          answerResultTable = chartData.result_table;
        }
        // График — на правую панель, в ленте только ссылка на него.
        // Панель открываем, только если пользователь смотрит на эту сессию.
        store.addTile(chartTile(chartData, t("chat.chartDefault")), sessionId);
        if (viewing()) useUiStore.getState().setCanvasOpen(true);
        addMessage({
          role: "agent",
          content: chartData.caption ? t("chat.chartDoneNamed", { name: chartData.caption }) : t("chat.chartDone"),
          chart: chartData,
        });
      } else if (event.type === "dashboard") {
        answerStarted = false;
        const dashData = (event.data || (event as any)) as DashboardEvent["data"];
        if (dashboard && dashData.dashboard_id) builtDashboardId = dashData.dashboard_id;
        addMessage({
          role: "agent",
          content: t("chat.dashboardDone", { name: dashData.title || t("chat.dashboardDefault") }),
          dashboard: dashData,
        });
        // Превью уже в ленте: панель не открываем автоматически, чтобы не
        // перекрывать чат. Пустые tiles не затирают текущий канвас.
        if (viewing() && dashData.tiles && dashData.tiles.length > 0) {
          useCanvasStore
            .getState()
            .setDashboard(dashData.dashboard_id, dashData.title || t("chat.dashboardDefault"), dashData.tiles);
        }
      } else if (event.type === "error") {
        answerStarted = false;
        fail();
        const raw = event as any;
        const msg =
          event.data?.message ||
          raw.message ||
          (typeof event.data?.detail === "string"
            ? event.data.detail
            : typeof raw.detail === "string"
            ? raw.detail
            : t("chat.genericError"));
        addMessage({ role: "agent", content: t("chat.error", { msg }), failed: true, failedQuestion: q, ...failDashboard });
      }
    },
    (error) => {
      finishProgress(Boolean(error) || !streamFinished || streamHadError);
      if (error) {
        fail();
        addMessage({
          role: "agent",
          content: t("chat.connectionLost", { msg: error }),
          failed: true,
          failedQuestion: retryQuestion,
          ...failDashboard,
        });
      } else if (!streamFinished && !streamHadError && !cancelled) {
        fail();
        addMessage({
          role: "agent",
          content: t("chat.streamIncomplete"),
          failed: true,
          failedQuestion: retryQuestion,
          ...failDashboard,
        });
      }
      stoppers.delete(sessionId);
      // Сессию могли удалить, пока шёл ход: тогда и статус не нужен
      if (!useChatRunStore.getState().runs[sessionId]) return;
      runs.patchRun(sessionId, {
        status: streamHadError ? "error" : "done",
        unseen: !cancelled && !isViewing(sessionId),
      });
    },
    { recoveryResponse, replyTo: options.replyTo, dashboard, resumeTurn: options.resumeTurn, clarifications },
  );
  stoppers.set(sessionId, () => {
    cancelled = true;
    stop();
  });
}

// Перезагрузка вкладки оборвёт все ходы: пока хоть один идёт, браузер переспросит.
if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", (e) => {
    if (Object.values(useChatRunStore.getState().runs).some((run) => run?.status === "running")) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
}
