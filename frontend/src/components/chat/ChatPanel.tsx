"use client";

/**
 * Панель диалога (Chat):
 * - Стриминг ответов агента (текст, инсайты)
 * - Графики и дашборды в ленту не выводятся: они уходят на правую панель,
 *   а в сообщении остаётся ссылка «Показать на панели» / «Открыть во вкладке»
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { getSession, setSessionTable, listTables, streamChat, exportArtifact } from "@/api/client";
import { useSessionStore, useSessionDatabase, type ChatMessage } from "@/store/sessionStore";
import { useCanvasStore } from "@/store/canvasStore";
import { useUiStore } from "@/store/uiStore";
import { useOpenArtifact } from "@/hooks/useOpenArtifact";
import { downloadBlob, safeFilename } from "@/lib/download";
import Icon from "@/components/ui/Icon";
import ClarificationCard from "@/components/chat/ClarificationCard";
import StepProgress from "@/components/chat/StepProgress";
import TableBriefCard from "@/components/chat/TableBriefCard";
import DashboardPreviewCard from "@/components/chat/DashboardPreviewCard";
import AnswerActions from "@/components/chat/AnswerActions";
import ReplyQuote from "@/components/chat/ReplyQuote";
import LlmStatusBanner from "@/components/chat/LlmStatusBanner";
import { useLlmStatus } from "@/hooks/useLlmStatus";
import { useT } from "@/i18n/useT";
import type { DashboardTile as DashboardTileType } from "@/types/dashboard";
import { RECOVERY_SKIP_STEP, applyProgress, type DashboardEvent, type TurnProgress } from "@/types/chat";

/** Плитка панели из события «chart»; id хранится в самом событии. */
function chartTile(chartData: any, fallbackTitle: string): DashboardTileType {
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

/** Текст ответа для буфера обмена: инсайт и детали, иначе — реплика из ленты. */
function answerText(m: ChatMessage): string {
  const lines: string[] = [];
  if (m.answer?.headline) lines.push(m.answer.headline);
  if (m.answer?.details?.length) lines.push(...m.answer.details.map((detail) => `• ${detail}`));
  return lines.length > 0 ? lines.join("\n") : m.content;
}

/** Короткая выдержка ответа для цитаты (схлопнутые пробелы, обрезка по границе слова). */
function excerptOf(m: ChatMessage, limit = 170): string {
  const raw = m.answer?.headline ?? m.chart?.caption ?? m.content ?? "";
  const text = raw.replace(/\s+/g, " ").trim();
  if (text.length <= limit) return text;
  const cut = text.slice(0, limit - 1);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > limit * 0.6 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`;
}

interface Props {
  sessionId: string;
}

export default function ChatPanel({ sessionId }: Props) {
  const router = useRouter();
  const t = useT();
  const llmStatus = useLlmStatus();
  // Статус не узнали (бэкенд не ответил) — не блокируем: ошибку покажет сам ход.
  const llmReady = !llmStatus || llmStatus.state === "ok";
  const setCanvasOpen = useUiStore((s) => s.setCanvasOpen);
  const openArtifact = useOpenArtifact();
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [lastFailedQuestion, setLastFailedQuestion] = useState<string | null>(null);
  const lastQuestionRef = useRef<string>("");
  // Сообщение с карточкой уточнения, на которую ещё можно ответить
  const [activeClarificationId, setActiveClarificationId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // Ответ, на который пользователь отвечает: уходит в reply_to (F-12).
  const [replyTarget, setReplyTarget] = useState<{ turn_id: string; excerpt: string } | null>(null);
  // Подсветка ответа, к которому перешли по цитате в ленте.
  const [highlightedTurnId, setHighlightedTurnId] = useState<string | null>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const messages = useSessionStore((s) => s.messages);
  const addMessage = useSessionStore((s) => s.addMessage);
  const appendToLastMessage = useSessionStore((s) => s.appendToLastMessage);
  const updateLastMessage = useSessionStore((s) => s.updateLastMessage);
  const updateMessage = useSessionStore((s) => s.updateMessage);
  const removeMessage = useSessionStore((s) => s.removeMessage);
  const removeLastFailedMessage = useSessionStore((s) => s.removeLastFailedMessage);
  const addTile = useSessionStore((s) => s.addTile);
  const registerSession = useSessionStore((s) => s.registerSession);
  const setStreamingSessionId = useSessionStore((s) => s.setStreamingSessionId);
  const pendingQuestion = useSessionStore((s) => s.pendingQuestions[sessionId]);
  const consumeQuestion = useSessionStore((s) => s.consumeQuestion);
  const activeStreamingSessionId = useSessionStore((s) => s.activeStreamingSessionId);
  const database = useSessionDatabase(sessionId);
  const sessionTitle = database?.sessions.find((x) => x.id === sessionId)?.title;

  const activeTable = useSessionStore((s) => s.sessionActiveTable[sessionId]);
  const activeBrief = database?.profiles?.find((brief) => brief.table === activeTable);
  const switchSessionTable = useSessionStore((s) => s.switchSessionTable);
  const bindSession = useSessionStore((s) => s.bindSession);

  const [remoteTables, setRemoteTables] = useState<string[]>([]);
  const tablesVersion = useUiStore((s) => s.tablesVersion);
  useEffect(() => {
    let cancelled = false;
    listTables()
      .then((tables) => {
        if (!cancelled && Array.isArray(tables)) setRemoteTables(tables);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [tablesVersion]);

  const availableTables = useMemo(() => {
    const list = [...(database?.tables ?? []), ...remoteTables];
    return Array.from(new Set(list)).filter(Boolean);
  }, [database?.tables, remoteTables]);

  const [isTableSwitching, setIsTableSwitching] = useState(false);
  const [tableError, setTableError] = useState<string | null>(null);

  const setCanvasDashboard = useCanvasStore((s) => s.setDashboard);

  useEffect(() => {
    if (!activeTable && sessionId) {
      getSession(sessionId)
        .then((sess) => {
          if (sess.table) {
            const dbId = useSessionStore.getState().sessionDatabase[sessionId] || sess.datasource_id || "";
            bindSession(sessionId, dbId, sess.table);
          }
        })
        .catch((err) => {
          if (err?.status === 404) {
            setTableError(t("chat.tableNotFound"));
          }
        });
    }
  }, [sessionId, activeTable, bindSession, t]);

  async function handleTableChange(nextTable: string) {
    if (!nextTable || nextTable === activeTable || isTableSwitching || streaming) return;
    setIsTableSwitching(true);
    setTableError(null);
    try {
      await setSessionTable(sessionId, nextTable);
      switchSessionTable(sessionId, nextTable);
      if (database) {
        bindSession(sessionId, database.id, nextTable);
      }
    } catch (err: any) {
      if (err?.status === 404) {
        setTableError(t("chat.tableNotFound"));
      } else {
        setTableError(err?.detail || err?.message || t("chat.tableNotFound"));
      }
    } finally {
      setIsTableSwitching(false);
    }
  }

  // Отмена стрима
  const stopRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    return () => {
      stopRef.current?.();
      setStreamingSessionId(null);
    };
  }, [setStreamingSessionId]);

  useEffect(() => {
    if (!streaming) return;
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [streaming]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming]);

  // Цитата не должна «протечь» в другую сессию или таблицу.
  useEffect(() => {
    setReplyTarget(null);
  }, [sessionId, activeTable]);

  // После «Спросить об этом» сразу ставим курсор в поле ввода.
  useEffect(() => {
    if (replyTarget) inputRef.current?.focus();
  }, [replyTarget]);

  useEffect(
    () => () => {
      if (highlightTimer.current) clearTimeout(highlightTimer.current);
    },
    [],
  );

  /** Перейти к ответу, на который ссылается цитата в ленте. */
  function scrollToTurn(turnId: string) {
    if (typeof document === "undefined") return;
    const target = document.getElementById(`turn-${turnId}`);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlightedTurnId(turnId);
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
    highlightTimer.current = setTimeout(() => setHighlightedTurnId(null), 1600);
  }

  /**
   * Отправить вопрос. recoveryResponse — ответ на уточнение: тогда q — то, что
   * видит пользователь в ленте, а бэкенд продолжает исходный вопрос.
   */
  function sendQuestion(
    textToSend?: string,
    optionsOrRecovery?:
      | string
      | {
          isRetry?: boolean;
          messageIdToReplace?: string;
          recoveryResponse?: string;
          /** Явный turn_id для reply_to; по умолчанию — выбранная цитата. */
          replyTo?: string | null;
        },
  ) {
    if (!activeTable) {
      setTableError(t("chat.noTableWarning"));
      return;
    }
    const options =
      typeof optionsOrRecovery === "string"
        ? { recoveryResponse: optionsOrRecovery }
        : optionsOrRecovery;
    const recoveryResponse = options?.recoveryResponse;
    const q = textToSend || input;
    if (!q.trim() || streaming) return;

    setActiveClarificationId(null);
    if (!recoveryResponse) lastQuestionRef.current = q;
    // Повтор после сбоя ответа на уточнение — исходный вопрос, а не текст ответа
    const retryQuestion = recoveryResponse ? lastQuestionRef.current : q;
    setLastFailedQuestion(null);

    // Цитата для reply_to: новый вопрос ссылается на выбранный ответ, повтор —
    // на цитату повторяемого вопроса. Уточнению reply_to не нужен: бэкенд сам
    // продолжает ход через pending_recovery.
    const quoteFromRetry = (): { turn_id: string; excerpt: string } | null => {
      const all = useSessionStore.getState().messages;
      const stop = options?.messageIdToReplace
        ? all.findIndex((x) => x.id === options.messageIdToReplace)
        : all.length;
      const prevUser = all
        .slice(0, stop >= 0 ? stop : all.length)
        .reverse()
        .find((x) => x.role === "user");
      return prevUser?.reply_to ?? null;
    };
    let quote: { turn_id: string; excerpt: string } | null = null;
    if (!recoveryResponse) {
      if (options?.isRetry) {
        quote = quoteFromRetry();
      } else if (options?.replyTo !== undefined) {
        if (options.replyTo) {
          // Явный turn_id: выдержку берём у самого ответа, если он в ленте.
          const source = useSessionStore
            .getState()
            .messages.find((x) => x.turn_id === options.replyTo);
          quote = {
            turn_id: options.replyTo,
            excerpt: source ? excerptOf(source) : replyTarget?.excerpt ?? "",
          };
        } else {
          quote = null;
        }
      } else {
        quote = replyTarget;
      }
    }

    if (options?.isRetry) {
      if (options.messageIdToReplace) {
        removeMessage(options.messageIdToReplace);
      } else {
        removeLastFailedMessage();
      }
      const curMessages = useSessionStore.getState().messages;
      const lastMsg = curMessages[curMessages.length - 1];
      if (!lastMsg || lastMsg.role !== "user" || lastMsg.content !== q) {
        addMessage({ role: "user", content: q, reply_to: quote ?? undefined });
      }
    } else {
      addMessage({ role: "user", content: q, reply_to: quote ?? undefined });
    }

    // Первая реплика: сессия появляется в папке своей базы данных
    registerSession(sessionId, q.length > 48 ? `${q.slice(0, 47)}…` : q);
    if (!textToSend) setInput("");
    setReplyTarget(null);
    setStreaming(true);
    setStreamingSessionId(sessionId);

    let answerStarted = false;
    let streamHadError = false;
    // Поток без done и без уточнения — оборван, это ошибка
    let streamFinished = false;
    let cancelled = false;
    // Прогресс хода живёт в отдельном сообщении над ответом
    let progress: TurnProgress | null = null;
    const progressId = `msg-progress-${Date.now().toString(36)}`;
    const finishProgress = () => {
      if (progress && progress.finished_at === undefined) {
        const now = Date.now();
        const timeline = progress.timeline
          ? progress.timeline.map((item) => {
              if (item.kind === "stage" && item.finished_at === undefined) {
                return { ...item, finished_at: now, elapsed_ms: Math.max(1, now - item.started_at) };
              }
              return item;
            })
          : undefined;
        progress = { ...progress, finished_at: now, timeline };
        updateMessage(progressId, { progress });
      }
    };
    const appendText = (text: string) => {
      if (answerStarted) {
        appendToLastMessage(text);
      } else {
        answerStarted = true;
        addMessage({ role: "agent", content: text });
      }
    };

    let textQueue = "";
    let streamTimer: ReturnType<typeof setInterval> | null = null;

    const processTextQueue = () => {
      if (!textQueue) {
        if (streamTimer) {
          clearInterval(streamTimer);
          streamTimer = null;
        }
        return;
      }
      const chunkSize = Math.max(3, Math.min(12, Math.ceil(textQueue.length / 10)));
      const chunk = textQueue.slice(0, chunkSize);
      textQueue = textQueue.slice(chunkSize);
      appendText(chunk);
    };

    const streamText = (text: string) => {
      textQueue += text;
      if (!streamTimer) {
        processTextQueue();
        streamTimer = setInterval(processTextQueue, 20);
      }
    };

    const flushTextQueue = () => {
      if (streamTimer) {
        clearInterval(streamTimer);
        streamTimer = null;
      }
      if (textQueue) {
        const remaining = textQueue;
        textQueue = "";
        appendText(remaining);
      }
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
      const all = useSessionStore.getState().messages;
      const last = all[all.length - 1];
      if (
        !last ||
        last.role !== "agent" ||
        last.failed ||
        last.clarification ||
        last.refusal
      ) {
        return;
      }
      updateLastMessage({
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
      });
    };

    const stop = streamChat(
      sessionId,
      q,
      (event) => {
        if (event.type === "done") {
          flushTextQueue();
          streamFinished = true;
          finishProgress();
          saveAnswerMeta(event.data?.turn_id);
        } else if (event.type === "progress") {
          if (!progress) {
            progress = applyProgress({ planning: false, steps: [], started_at: Date.now() }, event.data);
            answerStarted = false;
            addMessage({ id: progressId, role: "agent", content: "", progress });
          } else {
            progress = applyProgress(progress, event.data);
            updateMessage(progressId, { progress });
          }
        } else if (event.type === "clarification_needed") {
          flushTextQueue();
          // Бэкенд закрывает поток без done: ждёт recovery_response
          answerStarted = false;
          streamFinished = true;
          const id = `msg-clarify-${Date.now().toString(36)}`;
          addMessage({ id, role: "agent", content: "", clarification: event.data });
          setActiveClarificationId(id);
        } else if (event.type === "refusal") {
          flushTextQueue();
          answerStarted = false;
          streamFinished = true;
          addMessage({ role: "agent", content: event.data.reason || t("chat.refusal"), refusal: event.data });
        } else if (event.type === "text") {
          const raw = event as any;
          const headline = event.data?.headline || raw.headline;
          if (typeof headline === "string" && headline) answerHeadline = headline;
          const content = event.data?.headline || event.data?.chunk || raw.headline || raw.chunk || "";
          const details = event.data?.details || raw.details;
          let textToAppend = content;
          if (details && Array.isArray(details)) {
            answerDetails = details.filter((d: unknown): d is string => typeof d === "string");
            if (answerDetails.length > 0) {
              textToAppend += (textToAppend ? "\n\n" : "") + answerDetails.map((d: string) => `• ${d}`).join("\n");
            }
          }
          if (textToAppend) streamText(textToAppend);
        } else if (event.type === "chart") {
          flushTextQueue();
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
          // График — на правую панель, в ленте только ссылка на него
          addTile(chartTile(chartData, t("chat.chartDefault")));
          setCanvasOpen(true);
          addMessage({
            role: "agent",
            content: chartData.caption ? t("chat.chartDoneNamed", { name: chartData.caption }) : t("chat.chartDone"),
            chart: chartData,
          });
        } else if (event.type === "dashboard") {
          answerStarted = false;
          const dashData = (event.data || (event as any)) as DashboardEvent["data"];
          addMessage({
            role: "agent",
            content: t("chat.dashboardDone", { name: dashData.title || t("chat.dashboardDefault") }),
            dashboard: dashData,
          });
          // Превью уже в ленте: панель не открываем автоматически, чтобы не
          // перекрывать чат. Пустые tiles не затирают текущий канвас.
          if (dashData.tiles && dashData.tiles.length > 0) {
            setCanvasDashboard(dashData.dashboard_id, dashData.title || t("chat.dashboardDefault"), dashData.tiles);
          }
        } else if (event.type === "error") {
          answerStarted = false;
          streamHadError = true;
          setLastFailedQuestion(retryQuestion);
          const raw = event as any;
          const msg =
            event.data?.message ||
            raw.message ||
            (typeof event.data?.detail === "string"
              ? event.data.detail
              : typeof raw.detail === "string"
              ? raw.detail
              : t("chat.genericError"));
          addMessage({ role: "agent", content: t("chat.error", { msg }), failed: true, failedQuestion: q });
        }
      },
      (error) => {
        flushTextQueue();
        finishProgress();
        if (error) {
          streamHadError = true;
          setLastFailedQuestion(retryQuestion);
          addMessage({ role: "agent", content: t("chat.connectionLost", { msg: error }), failed: true, failedQuestion: retryQuestion });
        } else if (!streamFinished && !streamHadError && !cancelled) {
          setLastFailedQuestion(retryQuestion);
          addMessage({ role: "agent", content: t("chat.streamIncomplete"), failed: true, failedQuestion: retryQuestion });
        } else if (!streamHadError) {
          setLastFailedQuestion(null);
        }
        stopRef.current = null;
        setStreaming(false);
        setStreamingSessionId(null);
      },
      { recoveryResponse, replyTo: quote?.turn_id ?? null },
    );
    stopRef.current = () => {
      flushTextQueue();
      cancelled = true;
      stop();
      setStreamingSessionId(null);
    };
  }

  // Вопрос извне (клик по графику / «Дообследовать», F-09): отправляем, когда
  // таблица сессии уже известна и агент свободен. handledQuestionRef защищает
  // от повторной отправки в StrictMode и при ре-рендерах.
  const sendQuestionRef = useRef(sendQuestion);
  sendQuestionRef.current = sendQuestion;
  const handledQuestionRef = useRef<string | null>(null);

  useEffect(() => {
    if (!pendingQuestion) {
      handledQuestionRef.current = null;
      return;
    }
    if (handledQuestionRef.current === pendingQuestion.id) return;
    if (!activeTable || streaming || activeStreamingSessionId) return;
    handledQuestionRef.current = pendingQuestion.id;
    consumeQuestion(sessionId, pendingQuestion.id);
    // Внешний вопрос не продолжает выбранную цитату: replyTo: null перекрывает
    // значение из замыкания sendQuestion (setReplyTarget ещё не применился).
    setReplyTarget(null);
    sendQuestionRef.current(pendingQuestion.text, { replyTo: null });
  }, [
    pendingQuestion,
    activeTable,
    streaming,
    activeStreamingSessionId,
    consumeQuestion,
    sessionId,
  ]);

  function handleOpenDashboard(dashboardId: string) {
    router.push(`/dashboard/${dashboardId}`);
  }

  function handleLoadToCanvas(dashboardData: DashboardEvent["data"]) {
    const tiles = dashboardData.tiles ?? [];
    // Пустой набор не затирает уже собранный канвас — просто открываем панель
    if (tiles.length > 0) {
      setCanvasDashboard(
        dashboardData.dashboard_id,
        dashboardData.title || t("chat.dashboardDefault"),
        tiles,
      );
    }
    setCanvasOpen(true);
  }

  const [exportingMessageId, setExportingMessageId] = useState<string | null>(null);

  async function handleExportMessagePdf(msg: ChatMessage, index: number) {
    if (!msg.chart) return;
    const msgId = msg.id || String(index);
    setExportingMessageId(msgId);
    try {
      const prevUserQ = messages
        .slice(0, index)
        .reverse()
        .find((x) => x.role === "user")?.content;
      const { blob, filename } = await exportArtifact(
        {
          session_id: sessionId,
          format: "pdf",
          result_ref: msg.chart.result_table,
          question: prevUserQ,
          headline: msg.chart.caption,
          details: msg.content ? [msg.content] : undefined,
          spec: msg.chart.spec,
        },
        safeFilename(msg.chart.caption || t("chat.exportFallbackName"), "pdf"),
      );
      downloadBlob(blob, filename);
    } catch (err: any) {
      console.error("PDF export error:", err);
    } finally {
      setExportingMessageId(null);
    }
  }

  return (
    <div className="flex h-full flex-col bg-graphite-900 text-graphite-100">
      {/* Шапка чата: база данных и переключение таблицы */}
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-graphite-800 bg-graphite-950/60 px-4 md:px-8">
        <div className="flex items-center gap-2 min-w-0">
          <Icon name="folder" size={13} className="text-graphite-400 shrink-0" />
          <span className="text-[12px] text-graphite-400 truncate max-w-[140px] md:max-w-[200px]" title={database?.title}>
            {database?.title ?? t("header.database")}
          </span>
          <span className="text-graphite-600 text-xs">/</span>
          <span className="text-[12px] text-graphite-400 shrink-0">{t("chat.activeTable")}:</span>
          {availableTables.length > 0 ? (
            <select
              value={activeTable || ""}
              onChange={(e) => handleTableChange(e.target.value)}
              disabled={isTableSwitching || streaming}
              className="rounded-md border border-graphite-700 bg-graphite-850 px-2 py-0.5 text-xs font-mono text-graphite-100 outline-none focus:border-graphite-500 cursor-pointer disabled:opacity-50"
            >
              {!activeTable && (
                <option value="" disabled>
                  {t("chat.chooseTable")}
                </option>
              )}
              {availableTables.map((tbl) => (
                <option key={tbl} value={tbl}>
                  {tbl}
                </option>
              ))}
            </select>
          ) : (
            <span className="rounded-md bg-graphite-800/80 px-2 py-0.5 font-mono text-xs text-graphite-200">
              {activeTable || "—"}
            </span>
          )}
        </div>
        {tableError && (
          <div className="flex items-center gap-1.5 text-xs text-bad bg-bad/10 border border-bad/30 rounded-md px-2 py-0.5">
            <Icon name="x" size={12} className="cursor-pointer" onClick={() => setTableError(null)} />
            <span>{tableError}</span>
          </div>
        )}
      </div>

      {/* Лента сообщений */}
      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-6 md:px-8">
        {messages.length === 0 ? (
          <div className="mx-auto flex h-full max-w-xl flex-col justify-center">
            <div className="flex items-center gap-2 text-[13px] text-graphite-400">
              <Icon name="folder" />
              <span className="truncate">{database?.title ?? t("header.database")}</span>
            </div>
            <h2 className="mt-2 text-2xl font-light text-graphite-50">{sessionTitle ?? t("chat.newSession")}</h2>
            {database && database.tables.length > 0 && (
              <p className="mt-2 text-[13px] text-graphite-400">
                {t("chat.tables")}{" "}
                <span className="font-mono text-[12px] text-graphite-300">{database.tables.join(", ")}</span>
                {activeTable && (
                  <span className="ml-2 text-graphite-400">
                    ({t("chat.activeTable")}: <strong className="text-graphite-200 font-mono font-medium">{activeTable}</strong>)
                  </span>
                )}
              </p>
            )}
            {activeBrief && (
              <div className="mt-4">
                <TableBriefCard brief={activeBrief} />
              </div>
            )}
            <div className="mt-6 flex flex-col items-start gap-1">
              {[t("chat.suggest1"), t("chat.suggest2")].map((q) => (
                <button
                  key={q}
                  onClick={() => sendQuestion(q)}
                  disabled={!activeTable}
                  className="rounded-lg px-2 py-1 -mx-2 text-[13px] text-graphite-300 hover:bg-graphite-800 hover:text-graphite-50 transition disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {t("chat.quoted", { q })}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m, i) => (
            <div
              key={m.id || i}
              id={m.turn_id ? `turn-${m.turn_id}` : undefined}
              className={`mx-auto flex w-full max-w-3xl flex-col rounded-xl transition-shadow ${
                m.role === "user" ? "items-end" : "items-start"
              } ${m.turn_id && highlightedTurnId === m.turn_id ? "ring-1 ring-graphite-500/70" : ""}`}
            >
              {/* Цитата ответа, на который отвечает пользователь (клик — переход к ответу) */}
              {m.role === "user" && m.reply_to && (
                <ReplyQuote
                  variant="message"
                  excerpt={m.reply_to.excerpt}
                  onClick={() => scrollToTurn(m.reply_to!.turn_id)}
                />
              )}
              <div
                className={`text-sm leading-relaxed ${
                  m.role === "user"
                    ? "max-w-[85%] rounded-2xl bg-graphite-800 px-4 py-2.5 text-graphite-50"
                    : "w-full text-graphite-200"
                }`}
              >
                {m.progress && <StepProgress progress={m.progress} />}

                {/* Текст сообщения */}
                {m.content && (
                  <div
                    className={`whitespace-pre-wrap transition-opacity duration-200 ${m.progress ? "mt-2.5" : ""} ${
                      m.refusal ? "border-l-2 border-warn/60 pl-3 text-graphite-300" : ""
                    }`}
                  >
                    {m.content}
                    {streaming && i === messages.length - 1 && (
                      <span className="inline-block w-1.5 h-4 ml-1 bg-good/80 animate-pulse align-middle rounded-[1px]" />
                    )}
                  </div>
                )}

                {/* Уточнение после сбоя шага */}
                {m.clarification && (
                  <ClarificationCard
                    data={m.clarification}
                    active={!streaming && m.id === activeClarificationId}
                    onSkip={() => sendQuestion(t("chat.skipStep"), RECOVERY_SKIP_STEP)}
                    // step_id < 0 — план пуст, бэкенд не ждёт уточнения: это новый вопрос
                    onAnswer={(text) => sendQuestion(text, (m.clarification?.step_id ?? 0) >= 0 ? text : undefined)}
                  />
                )}

                {/* Отказ: переформулировки отправляются как новый вопрос */}
                {m.refusal && m.refusal.suggestions?.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {m.refusal.suggestions.map((suggestion) => (
                      <button
                        key={suggestion}
                        onClick={() => sendQuestion(suggestion)}
                        disabled={streaming}
                        className="rounded-full border border-graphite-600 px-3 py-1 text-xs text-graphite-200 hover:bg-graphite-800 disabled:opacity-50 transition"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                )}

                {/* Кнопка повтора при ошибке или обрыве */}
                {m.failed && !streaming && (
                  <div className="mt-3 pt-2 border-t border-bad/30 flex flex-col gap-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[11px] text-bad">{t("chat.failed")}</span>
                      <button
                        onClick={() => {
                          const questionToRetry =
                            m.failedQuestion ||
                            messages.slice(0, i).reverse().find((x) => x.role === "user")?.content ||
                            lastFailedQuestion ||
                            lastQuestionRef.current;
                          sendQuestion(questionToRetry, { isRetry: true, messageIdToReplace: m.id });
                        }}
                        className="inline-flex items-center gap-1.5 rounded-lg bg-danger hover:bg-danger/90 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition cursor-pointer"
                      >
                        <Icon name="refresh" size={14} />
                        <span>{t("chat.retry")}</span>
                      </button>
                    </div>
                    {m.suggestions && m.suggestions.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 mt-1">
                        {m.suggestions.map((sug) => (
                          <button
                            key={sug}
                            onClick={() => {
                              const questionToRetry =
                                m.failedQuestion ||
                                messages.slice(0, i).reverse().find((x) => x.role === "user")?.content ||
                                lastFailedQuestion ||
                                lastQuestionRef.current;
                              const recoveryText = JSON.stringify({
                                action: sug === "Пропустить этот шаг и продолжить" ? "skip_step" : "clarify",
                                text: sug,
                              });
                              sendQuestion(questionToRetry, {
                                isRetry: true,
                                messageIdToReplace: m.id,
                                recoveryResponse: recoveryText,
                              });
                            }}
                            className="rounded-md border border-graphite-700 bg-graphite-800 px-2 py-1 text-[11px] text-graphite-200 hover:bg-graphite-700 hover:text-white transition cursor-pointer"
                          >
                            {sug}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* График — только ссылка: сам он на правой панели */}
                {m.chart && (
                  <div className="mt-2 flex max-w-md items-center gap-2.5 rounded-lg border border-graphite-800 bg-graphite-850 py-1.5 pl-3 pr-1.5">
                    <Icon name="chart" className="shrink-0 text-graphite-400" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12.5px] text-graphite-100">
                        {m.chart.caption || t("chat.chartDefault")}
                      </span>
                      <span className="block text-[11px] text-graphite-500">
                        {t("chat.chartOnPanel")}
                      </span>
                    </span>
                    <button
                      onClick={() => handleExportMessagePdf(m, i)}
                      disabled={exportingMessageId === (m.id || String(i))}
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100 disabled:opacity-50 transition"
                      title={t("canvas.exportPdf")}
                    >
                      <Icon name="download" size={14} />
                    </button>
                    <button
                      onClick={() => setCanvasOpen(true)}
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100"
                      title={t("chat.showOnPanel")}
                    >
                      <Icon name="panelRight" size={15} />
                    </button>
                    <button
                      onClick={() => openArtifact(chartTile(m.chart, t("chat.chartDefault")), sessionId)}
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100"
                      title={t("chat.openInTab")}
                    >
                      <Icon name="maximize" size={14} />
                    </button>
                  </div>
                )}

                {/* Дашборд — компактная сетка плиток прямо из SSE-события */}
                {m.dashboard && (
                  <DashboardPreviewCard
                    dashboard={m.dashboard}
                    onOpenDashboard={() => handleOpenDashboard(m.dashboard!.dashboard_id)}
                    onOpenCanvas={() => handleLoadToCanvas(m.dashboard!)}
                  />
                )}

                {/* Действия под ответом: копирование и ссылка на ответ (F-12) */}
                {m.role === "agent" &&
                  !m.failed &&
                  !m.progress &&
                  !m.clarification &&
                  !m.refusal &&
                  !(streaming && i === messages.length - 1) &&
                  (m.turn_id || m.answer || m.chart) && (
                    <AnswerActions
                      text={answerText(m)}
                      canAsk={Boolean(m.turn_id)}
                      onAskAbout={() => {
                        if (!m.turn_id) return;
                        setReplyTarget({ turn_id: m.turn_id, excerpt: excerptOf(m) });
                        inputRef.current?.focus();
                      }}
                    />
                  )}
              </div>
              <span className="mt-1 text-[10px] text-graphite-500 px-1">
                {m.timestamp || ""}
              </span>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Поле ввода (как композер Claude Code) */}
      <div className="mx-auto w-full max-w-3xl px-4 pb-4 md:px-8">
        <LlmStatusBanner status={llmStatus} />
        {!activeTable && (
          <div className="mb-3 flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3.5 py-2.5 text-xs text-amber-200">
            <Icon name="alertTriangle" size={14} className="shrink-0 text-amber-400" />
            <span>{t("chat.noTableWarning")}</span>
          </div>
        )}
        {lastFailedQuestion && !streaming && (
          <div className="mb-3 flex items-center justify-between gap-3 rounded-xl border border-bad/30 bg-bad/10 px-3.5 py-2 text-xs text-bad">
            <div className="flex items-center gap-2 overflow-hidden">
              <span className="truncate">{t("chat.failedBanner", { q: lastFailedQuestion })}</span>
            </div>
            <button
              onClick={() => sendQuestion(lastFailedQuestion, { isRetry: true })}
              disabled={!activeTable}
              className="inline-flex items-center gap-1.5 rounded-lg bg-danger hover:bg-danger/90 px-3 py-1.5 text-xs font-semibold text-white shadow transition whitespace-nowrap disabled:opacity-50 cursor-pointer"
            >
              <Icon name="refresh" size={14} />
              <span>{t("chat.retry")}</span>
            </button>
          </div>
        )}
        <div className="rounded-2xl border border-graphite-700 bg-graphite-850 p-2 pl-4 shadow-sm transition focus-within:border-graphite-500">
          {/* Чип-цитата: вопрос уйдёт с reply_to выбранного ответа */}
          {replyTarget && (
            <ReplyQuote
              variant="composer"
              excerpt={replyTarget.excerpt}
              onClear={() => setReplyTarget(null)}
            />
          )}
          <div className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                // Поле растёт по содержимому до max-h-40
                e.target.style.height = "auto";
                e.target.style.height = `${e.target.scrollHeight}px`;
                e.target.style.overflowY = e.target.scrollHeight > 160 ? "auto" : "hidden";
              }}
              onKeyDown={(e) => {
                // Esc при пустом вводе убирает цитату
                if (e.key === "Escape" && replyTarget && !input.trim()) {
                  e.preventDefault();
                  setReplyTarget(null);
                  return;
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (activeTable && llmReady) sendQuestion();
                }
              }}
              rows={1}
              placeholder={
                !activeTable
                  ? t("chat.noTableWarning")
                  : !llmReady
                  ? t("llm.waiting")
                  : replyTarget
                  ? t("chat.replyPlaceholder")
                  : activeClarificationId
                  ? t("chat.clarifyPlaceholder")
                  : t("chat.placeholder")
              }
              className="max-h-40 min-h-[36px] flex-1 resize-none overflow-y-hidden bg-transparent py-2 text-sm leading-5 text-graphite-100 placeholder-graphite-500 outline-none disabled:cursor-not-allowed disabled:opacity-60"
              disabled={streaming || !activeTable}
            />
            {streaming ? (
              <button
                onClick={() => stopRef.current?.()}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-graphite-700 text-graphite-100 hover:bg-graphite-600 transition"
                title={t("chat.stop")}
              >
                <Icon name="stop" size={14} />
              </button>
            ) : (
              <button
                onClick={() => sendQuestion()}
                disabled={!input.trim() || !activeTable || !llmReady}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-graphite-100 text-graphite-950 hover:bg-graphite-50 disabled:bg-graphite-700 disabled:text-graphite-500 disabled:cursor-not-allowed transition"
                title={t("chat.send")}
              >
                <Icon name="arrowUp" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
