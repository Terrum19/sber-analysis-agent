"use client";

/**
 * Панель диалога (Chat):
 * - Стриминг ответов агента (текст, инсайты)
 * - Графики и дашборды в ленту не выводятся: они уходят на правую панель,
 *   а в сообщении остаётся ссылка «Показать на панели» / «Открыть во вкладке»
 * - Режим дашборда (#224): переключатель под строкой ввода, варианты по кнопке
 *   «Автоматическая генерация», ход с mode: "dashboard" открывает /dashboard/{id}
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  getSession,
  setSessionTable,
  listTables,
  exportArtifact,
  renameTableApi,
  fetchDashboardIdeas,
  clarifyQuestions,
  type DashboardRequest,
} from "@/api/client";
import { useSessionStore, useSessionDatabase, tableTitle, type ChatMessage } from "@/store/sessionStore";
import { useCanvasStore } from "@/store/canvasStore";
import { chartTile, startRun, stopRun, useChatRunStore } from "@/store/chatRunStore";
import { useUiStore } from "@/store/uiStore";
import { useOpenArtifact } from "@/hooks/useOpenArtifact";
import { useTableBrief } from "@/hooks/useTableBrief";
import { downloadBlob, safeFilename } from "@/lib/download";
import Icon from "@/components/ui/Icon";
import ClarificationCard from "@/components/chat/ClarificationCard";
import StepProgress from "@/components/chat/StepProgress";
import ReviewCard, { DraftAnswer } from "@/components/chat/ReviewCard";
import TableBriefCard from "@/components/chat/TableBriefCard";
import DashboardPreviewCard from "@/components/chat/DashboardPreviewCard";
import AnswerActions from "@/components/chat/AnswerActions";
import ReplyQuote from "@/components/chat/ReplyQuote";
import RecipeBanner from "@/components/chat/RecipeBanner";
import StepsList from "@/components/chat/StepsList";
import LlmStatusBanner from "@/components/chat/LlmStatusBanner";
import DashboardIdeas, { type IdeasState } from "@/components/chat/DashboardIdeas";
import MarkdownContent from "@/components/chat/MarkdownContent";
import ClarifyCard from "@/components/chat/ClarifyCard";
import { useLlmStatus } from "@/hooks/useLlmStatus";
import { useT } from "@/i18n/useT";
import {
  RECOVERY_SKIP_STEP,
  type Clarification,
  type ClarifyCardData,
  type DashboardEvent,
  type DashboardIdea,
  type TurnProgress,
} from "@/types/chat";

/** Сколько раз подряд просить новые варианты дашборда, если все прежние уже использованы. */
const MAX_IDEA_ROUNDS = 3;

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
  // В content ответ в Markdown: в цитату — без звёздочек и обратных кавычек
  const text = raw.replace(/\*\*|`/g, "").replace(/\s+/g, " ").trim();
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
  // Ход агента живёт в chatRunStore: уход из чата его не обрывает
  const run = useChatRunStore((s) => s.runs[sessionId]);
  const markSeen = useChatRunStore((s) => s.markSeen);
  const streaming = run?.status === "running";
  const lastFailedQuestion = run?.failedQuestion ?? null;
  const [dashboardMode, setDashboardMode] = useState(false);
  // Режим «Проверка» (A-29): после ответа агент проверяет его и при необходимости исправляет
  const [verifyMode, setVerifyMode] = useState(false);
  // Глубокое исследование (A-28): держится до конца сессии, с дашбордом не совмещается
  const deepMode = useSessionStore((s) => Boolean(s.deepResearch[sessionId]));
  const setDeepResearch = useSessionStore((s) => s.setDeepResearch);
  const [ideas, setIdeas] = useState<IdeasState | null>(null);
  const ideasRequestRef = useRef(0);
  // Сообщение с карточкой уточнения, на которую ещё можно ответить
  const activeClarificationId = run?.clarificationId ?? null;
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // Ответ, на который пользователь отвечает: уходит в reply_to (F-12).
  const [replyTarget, setReplyTarget] = useState<{ turn_id: string; excerpt: string } | null>(null);
  // Подсветка ответа, к которому перешли по цитате в ленте.
  const [highlightedTurnId, setHighlightedTurnId] = useState<string | null>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const messages = useSessionStore((s) => s.messages);
  // Агент готовит уточняющие вопросы: новый вопрос подождёт
  const clarifying = messages.some((m) => m.clarify?.state.status === "loading");
  const addMessage = useSessionStore((s) => s.addMessage);
  const removeMessage = useSessionStore((s) => s.removeMessage);
  const removeLastFailedMessage = useSessionStore((s) => s.removeLastFailedMessage);
  const registerSession = useSessionStore((s) => s.registerSession);
  const pendingQuestion = useSessionStore((s) => s.pendingQuestions[sessionId]);
  const consumeQuestion = useSessionStore((s) => s.consumeQuestion);
  const markIdeaUsed = useSessionStore((s) => s.markIdeaUsed);
  const usedIdeas = useSessionStore((s) => s.usedIdeas[sessionId]);
  const database = useSessionDatabase(sessionId);
  const sessionTitle = database?.sessions.find((x) => x.id === sessionId)?.title;

  const activeTable = useSessionStore((s) => s.sessionActiveTable[sessionId]);
  const activeBrief = database?.profiles?.find((brief) => brief.table === activeTable);
  const switchSessionTable = useSessionStore((s) => s.switchSessionTable);
  const bindSession = useSessionStore((s) => s.bindSession);
  const updateBrief = useSessionStore((s) => s.updateBrief);
  useTableBrief(database?.id, activeTable);
  const suggestions = activeBrief?.suggested_questions?.length
    ? activeBrief.suggested_questions
    : [t("chat.suggest1"), t("chat.suggest2")];

  /** Черновик названия активной таблицы; null — не редактируется. */
  const [tableDraft, setTableDraft] = useState<string | null>(null);

  async function saveTableTitle() {
    const name = tableDraft?.trim();
    setTableDraft(null);
    if (!name || !activeTable || !database || name === tableTitle(database, activeTable)) return;
    try {
      updateBrief(database.id, await renameTableApi(activeTable, name));
    } catch {
      setTableError(t("chat.renameTableError"));
    }
  }

  const [remoteTables, setRemoteTables] = useState<string[]>([]);
  const tablesVersion = useUiStore((s) => s.tablesVersion);
  const focusTurn = useUiStore((s) => s.focusTurn);
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
            setTableError(t("chat.sessionGone"));
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
        // 404 — нет таблицы или самой сессии: текст бэкенда говорит, чего именно
        setTableError(err?.detail || t("chat.tableNotFound"));
      } else {
        setTableError(err?.detail || err?.message || t("chat.tableNotFound"));
      }
    } finally {
      setIsTableSwitching(false);
    }
  }

  // Открытый чат — ответ увиден: точка «готово» / «ошибка» в сайдбаре гаснет
  useEffect(() => {
    if (run?.unseen) markSeen(sessionId);
  }, [run?.unseen, sessionId, markSeen]);

  // Мгновенная прокрутка к последнему сообщению при открытии сессии/переключении вкладок
  // и при новых сообщениях (без плавной «прокатки» всей истории с начала до конца)
  const scrollToBottom = () => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
    } else {
      messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
    }
  };

  useLayoutEffect(() => {
    scrollToBottom();
  }, [sessionId, messages, streaming]);

  useEffect(() => {
    scrollToBottom();
    const rafId = requestAnimationFrame(scrollToBottom);
    return () => cancelAnimationFrame(rafId);
  }, [sessionId, messages, streaming]);

  // Цитата и варианты дашборда не должны «протечь» в другую сессию или таблицу.
  useEffect(() => {
    setReplyTarget(null);
    setIdeas(null);
    ideasRequestRef.current += 1;
  }, [sessionId, activeTable]);

  /** «Автоматическая генерация»: варианты дашборда по таблице сессии. */
  /**
   * Варианты без уже использованных: обычная пачка из кэша бэкенда; если по всем
   * уже строили — новая пачка, не повторяющая ни одного прежнего варианта.
   */
  async function fetchFreshIdeas(): Promise<DashboardIdea[]> {
    const used = useSessionStore.getState().usedIdeas[sessionId] ?? [];
    const seen = new Set(used);
    let list = await fetchDashboardIdeas(sessionId);
    // Кэш бэкенда отдаёт одну и ту же пачку на один набор исключений: несколько кругов — предел
    for (let round = 0; round < MAX_IDEA_ROUNDS; round += 1) {
      const fresh = list.filter((idea) => !seen.has(idea.title));
      if (fresh.length > 0) return fresh;
      list.forEach((idea) => seen.add(idea.title));
      list = await fetchDashboardIdeas(sessionId, Array.from(seen));
    }
    return list.filter((idea) => !seen.has(idea.title));
  }

  function loadIdeas() {
    const request = ++ideasRequestRef.current;
    setIdeas({ status: "loading", started_at: Date.now() });
    fetchFreshIdeas()
      .then((list) => {
        if (ideasRequestRef.current === request) setIdeas({ status: "ready", ideas: list });
      })
      .catch((err: any) => {
        if (ideasRequestRef.current !== request) return;
        setIdeas({ status: "error", message: err?.detail || err?.message || "", noModel: err?.status === 502 });
      });
  }

  function closeIdeas() {
    ideasRequestRef.current += 1;
    setIdeas(null);
  }

  /** Ход дашборда по выбранному варианту: в ленте — название варианта; режим дашборда выбором исчерпан. */
  function sendIdea(idea: DashboardIdea) {
    closeIdeas();
    setDashboardMode(false);
    markIdeaUsed(sessionId, idea.title);
    sendQuestion(idea.title, { dashboard: { idea }, replyTo: null });
  }

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

  // «Открыть в чате» с вкладки инсайтов
  useEffect(() => {
    if (focusTurn) scrollToTurn(focusTurn.turnId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusTurn]);
  // «Открыть чат» с плитки дашборда: ?turn= — прокрутить к ответу, когда лента загрузится.
  const jumpTurnRef = useRef<string | null>(null);
  useEffect(() => {
    jumpTurnRef.current = new URLSearchParams(window.location.search).get("turn");
  }, [sessionId]);
  useEffect(() => {
    const turnId = jumpTurnRef.current;
    if (!turnId || !messages.some((m) => m.turn_id === turnId)) return;
    jumpTurnRef.current = null;
    window.history.replaceState(null, "", window.location.pathname);
    setTimeout(() => scrollToTurn(turnId), 400);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

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
          /** Ход строит дашборд; по умолчанию — если включён режим и вопрос из строки ввода. */
          dashboard?: DashboardRequest;
          /** Уточнения повторяемого хода глубокого исследования: вопросы заново не задаются. */
          clarifications?: Clarification[];
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
    if (!q.trim() || streaming || clarifying) return;
    const dashboard: DashboardRequest | undefined =
      options?.dashboard ?? (dashboardMode && !textToSend && !recoveryResponse ? { idea: null } : undefined);
    if (dashboard) closeIdeas();

    // Повтор после сбоя ответа на уточнение — исходный вопрос, а не текст ответа
    const retryQuestion = recoveryResponse ? run?.question || q : q;

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
    // Глубокое исследование: сначала уточняющие вопросы, ход — после ответа на них
    if (deepMode && !dashboard && !recoveryResponse && !options?.isRetry) {
      askClarify({ question: q, reply_to: quote?.turn_id ?? null, state: { status: "loading", started_at: Date.now() } });
      return;
    }
    startRun(sessionId, q, {
      retryQuestion,
      recoveryResponse,
      replyTo: quote?.turn_id ?? null,
      dashboard,
      verify: verifyMode && !dashboard,
      clarifications: options?.clarifications,
      onDashboardBuilt: (dashboardId) => router.push(`/dashboard/${dashboardId}`),
    });
  }

  /**
   * Уточняющие вопросы глубокого исследования (A-28): карточка в ленте, пока
   * хода нет. Вопроса хватает — карточка убирается и ход начинается сразу.
   * messageId — повтор после ошибки в той же карточке.
   */
  function askClarify(card: ClarifyCardData, messageId?: string) {
    const id = messageId ?? `msg-clarify-${Date.now().toString(36)}`;
    const store = useSessionStore.getState();
    if (messageId) {
      store.updateMessage(id, { clarify: card }, sessionId);
    } else {
      store.addMessage({ id, role: "agent", content: "", clarify: card }, sessionId);
    }
    const patch = (state: ClarifyCardData["state"]) =>
      useSessionStore.getState().updateMessage(id, { clarify: { ...card, state } }, sessionId);
    clarifyQuestions(sessionId, card.question, card.reply_to)
      .then((result) => {
        if (result.enough_info || result.questions.length === 0) {
          useSessionStore.getState().removeMessage(id, sessionId);
          runResearch(card);
        } else {
          patch({ status: "ready", questions: result.questions });
        }
      })
      .catch((err: any) => patch({ status: "error", message: err?.status === 502 ? "" : err?.detail || err?.message || "" }));
  }

  /** Ход глубокого исследования с ответами на вопросы карточки; пустой список — без уточнений. */
  function runResearch(card: ClarifyCardData, clarifications?: Clarification[], messageId?: string) {
    if (messageId) {
      useSessionStore
        .getState()
        .updateMessage(messageId, { clarify: { ...card, state: { status: "answered", clarifications: clarifications ?? [] } } }, sessionId);
    }
    const answered = clarifications && clarifications.length > 0 ? clarifications : undefined;
    startRun(sessionId, card.question, {
      retryQuestion: card.question,
      replyTo: card.reply_to,
      clarifications: answered,
      verify: verifyMode,
      onDashboardBuilt: (dashboardId) => router.push(`/dashboard/${dashboardId}`),
    });
  }

  /**
   * «Продолжить» остановленный или упавший ход: бэкенд досчитывает его с чекпоинта,
   * готовые шаги и плитки не повторяются. Новый вопрос в ленту не добавляется.
   */
  function resumeTurn(messageId: string, progress: TurnProgress) {
    if (streaming || !progress.turn_id) return;
    const all = useSessionStore.getState().messages;
    const index = all.findIndex((x) => x.id === messageId);
    const question =
      all
        .slice(0, index >= 0 ? index : all.length)
        .reverse()
        .find((x) => x.role === "user")?.content ?? "";
    // Кнопка у старого прогресса гаснет: ход продолжается в новом
    useSessionStore.getState().updateMessage(messageId, { progress: { ...progress, resumable: false } });
    startRun(sessionId, question, {
      retryQuestion: question,
      replyTo: null,
      resumeTurn: progress.turn_id,
      dashboard: progress.tiles ? { idea: null } : undefined,
      verify: verifyMode && !progress.tiles,
      onDashboardBuilt: (dashboardId) => router.push(`/dashboard/${dashboardId}`),
    });
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
    if (!activeTable || streaming) return;
    // Отправка — на следующем тике, с отменой в cleanup: StrictMode сразу
    // размонтирует и снова монтирует панель, и вопрос уходит один раз —
    // после повторного монтирования.
    const question = pendingQuestion;
    const timer = window.setTimeout(() => {
      handledQuestionRef.current = question.id;
      consumeQuestion(sessionId, question.id);
      // Внешний вопрос не продолжает выбранную цитату: replyTo: null перекрывает
      // значение из замыкания sendQuestion (setReplyTarget ещё не применился).
      setReplyTarget(null);
      sendQuestionRef.current(question.text, { replyTo: null });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [
    pendingQuestion,
    activeTable,
    streaming,
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
      const exportTitle = (msg.chart.caption || t("chat.exportFallbackName")).trim();
      const { blob, filename } = await exportArtifact(
        {
          session_id: sessionId,
          format: "pdf",
          result_ref: msg.chart.result_table,
          question: prevUserQ,
          headline: msg.chart.caption,
          details: msg.content ? [msg.content] : undefined,
          spec: msg.chart.spec,
          title: exportTitle,
        },
        safeFilename(exportTitle, "pdf"),
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
          <span
            className="text-[12px] text-graphite-400 truncate max-w-[140px] md:max-w-[200px]"
            title={database?.fileName && database.fileName !== database.title ? `${database.title}\n${database.fileName}` : database?.title}
          >
            {database?.title ?? t("header.database")}
          </span>
          <span className="text-graphite-600 text-xs">/</span>
          <span className="text-[12px] text-graphite-400 shrink-0">{t("chat.activeTable")}:</span>
          {availableTables.length > 0 ? (
            <select
              value={activeTable || ""}
              onChange={(e) => handleTableChange(e.target.value)}
              disabled={isTableSwitching || streaming}
              title={activeTable ? t("chat.tableSqlName", { name: activeTable }) : undefined}
              className="rounded-md border border-graphite-700 bg-graphite-850 px-2 py-0.5 text-xs text-graphite-100 outline-none focus:border-graphite-500 cursor-pointer disabled:opacity-50"
            >
              {!activeTable && (
                <option value="" disabled>
                  {t("chat.chooseTable")}
                </option>
              )}
              {availableTables.map((tbl) => (
                <option key={tbl} value={tbl}>
                  {tableTitle(database, tbl)}
                </option>
              ))}
            </select>
          ) : (
            <span
              className="rounded-md bg-graphite-800/80 px-2 py-0.5 text-xs text-graphite-200"
              title={activeTable ? t("chat.tableSqlName", { name: activeTable }) : undefined}
            >
              {activeTable ? tableTitle(database, activeTable) : "—"}
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
      <div ref={scrollContainerRef} className="flex-1 space-y-5 overflow-y-auto px-4 py-6 md:px-8">
        {/* Ссылка-рецепт: графики без точек пересчитываются по кнопке (#250) */}
        <RecipeBanner sessionId={sessionId} messages={messages} />
        {messages.length === 0 ? (
          <div className="mx-auto flex h-full max-w-xl flex-col justify-center">
            <div className="flex items-center gap-2 text-[13px] text-graphite-400">
              <Icon name="folder" />
              <span className="truncate">{database?.title ?? t("header.database")}</span>
            </div>
            {sessionTitle || !activeTable || !database ? (
              <h2 className="mt-2 text-2xl font-light text-graphite-50">{sessionTitle ?? t("chat.newSession")}</h2>
            ) : tableDraft !== null ? (
              <input
                autoFocus
                value={tableDraft}
                maxLength={60}
                onChange={(e) => setTableDraft(e.target.value)}
                onBlur={() => void saveTableTitle()}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                  if (e.key === "Escape") setTableDraft(null);
                }}
                className="mt-2 w-full rounded-md border border-graphite-700 bg-graphite-850 px-2 py-0.5 text-2xl font-light text-graphite-50 outline-none focus:border-graphite-500"
              />
            ) : (
              <div className="group mt-2 flex items-center gap-2">
                <h2 className="truncate text-2xl font-light text-graphite-50" title={t("chat.tableSqlName", { name: activeTable })}>
                  {tableTitle(database, activeTable)}
                </h2>
                <button
                  type="button"
                  onClick={() => setTableDraft(tableTitle(database, activeTable))}
                  disabled={database.demo}
                  className="shrink-0 rounded-md p-1 text-graphite-500 opacity-0 transition hover:bg-graphite-800 hover:text-graphite-100 group-hover:opacity-100 focus:opacity-100 disabled:hidden"
                  title={t("chat.renameTable")}
                >
                  <Icon name="pencil" size={14} />
                </button>
              </div>
            )}
            {database && database.tables.length > 1 && (
              <p className="mt-2 text-[13px] text-graphite-400">
                {t("chat.tables")}{" "}
                <span className="text-graphite-300">{database.tables.map((tbl) => tableTitle(database, tbl)).join(", ")}</span>
              </p>
            )}
            {activeBrief && (
              <div className="mt-4">
                <TableBriefCard brief={activeBrief} />
              </div>
            )}
            <div className="mt-6 flex flex-col items-start gap-1">
              {suggestions.map((q) => (
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
          messages.map((m, i) =>
            m.superseded ? (
              <DraftAnswer key={m.id || i} message={m} />
            ) : (
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
                {m.review && <ReviewCard review={m.review} sessionId={sessionId} />}

                {m.progress && (
                  <StepProgress
                    progress={m.progress}
                    sessionId={sessionId}
                    onResume={
                      m.progress.resumable && m.id && !streaming
                        ? () => resumeTurn(m.id!, m.progress!)
                        : undefined
                    }
                  />
                )}

                {/* Ответ исправлен после проверки (режим «Проверка») */}
                {m.revised && (
                  <div className="mb-1 inline-flex items-center gap-1 rounded-full bg-good/10 px-2 py-0.5 text-[11px] text-good">
                    <Icon name="check" size={11} />
                    {t("verify.updated")}
                  </div>
                )}

                {/* Уточняющие вопросы глубокого исследования */}
                {m.clarify && (
                  <ClarifyCard
                    data={m.clarify}
                    active={!streaming && i === messages.length - 1}
                    onStart={(clarifications) => runResearch(m.clarify!, clarifications, m.id)}
                    onRetry={() =>
                      askClarify({ ...m.clarify!, state: { status: "loading", started_at: Date.now() } }, m.id)
                    }
                  />
                )}

                {/* Текст сообщения */}
                {m.content && (
                  <div
                    className={
                      m.refusal
                        ? "whitespace-pre-wrap border-l-2 border-warn/60 pl-3 text-graphite-300"
                        : m.dashboardFallback
                        ? "whitespace-pre-wrap rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-graphite-200"
                        : m.role === "user"
                        ? "whitespace-pre-wrap"
                        : ""
                    }
                  >
                    {m.role === "agent" && !m.refusal && !m.dashboardFallback ? (
                      <MarkdownContent content={m.content} />
                    ) : (
                      m.content
                    )}
                    {/* Ответ ещё печатается: курсор под последним куском */}
                    {m.role === "agent" && !m.progress && streaming && i === messages.length - 1 && (
                      <span
                        aria-hidden
                        className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse rounded-[1px] bg-graphite-400 align-middle motion-reduce:animate-none"
                      />
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

                {/* Запроса не хватило: остальные варианты — новый ход с этой идеей */}
                {m.dashboardFallback &&
                  m.dashboardFallback.alternatives.some((alt) => !usedIdeas?.includes(alt.title)) && (
                  <div className="mt-2">
                    <div className="mb-1.5 text-[11.5px] text-graphite-500">{t("dashboard.otherIdeas")}</div>
                    <div className="flex flex-wrap gap-2">
                      {m.dashboardFallback.alternatives
                        .filter((alt) => !usedIdeas?.includes(alt.title))
                        .map((alt) => (
                        <button
                          key={alt.id || alt.title}
                          onClick={() => sendIdea(alt)}
                          disabled={streaming || !activeTable}
                          title={alt.description}
                          className="rounded-full border border-graphite-600 px-3 py-1 text-xs text-graphite-200 hover:bg-graphite-800 disabled:opacity-50 transition"
                        >
                          {alt.title}
                        </button>
                      ))}
                    </div>
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
                            run?.question;
                          sendQuestion(questionToRetry, {
                            isRetry: true,
                            messageIdToReplace: m.id,
                            dashboard: m.failedDashboard,
                            clarifications: m.failedClarifications,
                          });
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
                                run?.question;
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

                {/* Шаги хода автора из ссылки (#250): видно, что и как посчитано */}
                {m.steps && <StepsList steps={m.steps} />}

                {/* График из рецепта без точек: на панели его ещё нет */}
                {m.chart && (m.recipe === "pending" || m.recipe === "failed") && (
                  <div className="mt-2 flex max-w-md items-center gap-2.5 rounded-lg border border-dashed border-graphite-700 py-1.5 pl-3 pr-3">
                    <Icon name="chart" className="shrink-0 text-graphite-500" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12.5px] text-graphite-300">
                        {m.chart.caption || t("chat.chartDefault")}
                      </span>
                      <span className="block text-[11px] text-warn">
                        {m.recipe === "failed"
                          ? t("chat.recipeFailed", { msg: m.recipeError ?? "" })
                          : t("chat.recipePending")}
                      </span>
                    </span>
                  </div>
                )}

                {/* График — только ссылка: сам он на правой панели */}
                {m.chart && m.recipe !== "pending" && m.recipe !== "failed" && (
                  <div className="mt-2 flex max-w-md items-center gap-2.5 rounded-lg border border-graphite-800 bg-graphite-850 py-1.5 pl-3 pr-1.5">
                    <Icon name="chart" className="shrink-0 text-graphite-400" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12.5px] text-graphite-100">
                        {m.chart.caption || t("chat.chartDefault")}
                      </span>
                      <span className="block text-[11px] text-graphite-500">
                        {m.recipe === "replayed"
                          ? t("chat.recipeReplayed")
                          : m.recipe === "author"
                            ? t("chat.recipeAuthor")
                            : t("chat.chartOnPanel")}
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
                {/* Ход из ссылки другого пользователя (#249) */}
                {m.imported && m.role === "user" && (
                  <span className="ml-1.5 rounded border border-graphite-700 px-1 py-px text-graphite-400">
                    {t("chat.importedBadge")}
                  </span>
                )}
              </span>
            </div>
            ),
          )
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
              onClick={() =>
                sendQuestion(lastFailedQuestion, {
                  isRetry: true,
                  dashboard: run?.failedDashboard,
                  clarifications: run?.failedClarifications,
                })
              }
              disabled={!activeTable}
              className="inline-flex items-center gap-1.5 rounded-lg bg-danger hover:bg-danger/90 px-3 py-1.5 text-xs font-semibold text-white shadow transition whitespace-nowrap disabled:opacity-50 cursor-pointer"
            >
              <Icon name="refresh" size={14} />
              <span>{t("chat.retry")}</span>
            </button>
          </div>
        )}
        {dashboardMode && ideas && (
          <DashboardIdeas
            // Варианты, по которым дашборд уже строили, не предлагаем снова
            state={
              ideas.status === "ready"
                ? { ...ideas, ideas: ideas.ideas.filter((idea) => !usedIdeas?.includes(idea.title)) }
                : ideas
            }
            disabled={streaming || !activeTable || !llmReady}
            onPick={sendIdea}
            onOwn={() => {
              closeIdeas();
              inputRef.current?.focus();
            }}
            onRetry={loadIdeas}
            onClose={closeIdeas}
          />
        )}
        <div className="rounded-2xl border border-graphite-700 bg-graphite-850 p-2 pl-4 shadow-sm transition focus-within:border-graphite-500">
          {/* Чип режима дашборда: × выключает режим */}
          {dashboardMode && (
            <span className="mt-1 mb-1 inline-flex items-center gap-1 rounded-full border border-graphite-600 bg-graphite-800 py-0.5 pl-2 pr-1 text-[12px] text-graphite-100">
              {t("dashboard.chip")}
              <button
                onClick={() => {
                  setDashboardMode(false);
                  closeIdeas();
                }}
                className="flex h-4 w-4 items-center justify-center rounded-full text-graphite-400 hover:bg-graphite-700 hover:text-graphite-100"
                title={t("dashboard.chipOff")}
              >
                <Icon name="x" size={10} />
              </button>
            </span>
          )}
          {/* Чип режима проверки: × выключает режим */}
          {verifyMode && !dashboardMode && (
            <span className="mt-1 mb-1 mr-1 inline-flex items-center gap-1 rounded-full border border-graphite-600 bg-graphite-800 py-0.5 pl-2 pr-1 text-[12px] text-graphite-100">
              {t("verify.chip")}
              <button
                onClick={() => setVerifyMode(false)}
                className="flex h-4 w-4 items-center justify-center rounded-full text-graphite-400 hover:bg-graphite-700 hover:text-graphite-100"
                title={t("verify.chipOff")}
              >
                <Icon name="x" size={10} />
              </button>
            </span>
          )}
          {/* Чип глубокого исследования: × выключает режим */}
          {deepMode && (
            <span className="mt-1 mb-1 inline-flex items-center gap-1 rounded-full border border-graphite-600 bg-graphite-800 py-0.5 pl-2 pr-1 text-[12px] text-graphite-100">
              🔍 {t("research.chip")}
              <button
                onClick={() => setDeepResearch(sessionId, false)}
                className="flex h-4 w-4 items-center justify-center rounded-full text-graphite-400 hover:bg-graphite-700 hover:text-graphite-100"
                title={t("research.chipOff")}
              >
                <Icon name="x" size={10} />
              </button>
            </span>
          )}
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
                  : dashboardMode
                  ? t("dashboard.placeholder")
                  : deepMode
                  ? t("research.placeholder")
                  : t("chat.placeholder")
              }
              className="max-h-40 min-h-[36px] flex-1 resize-none overflow-y-hidden bg-transparent py-2 text-sm leading-5 text-graphite-100 placeholder-graphite-500 outline-none disabled:cursor-not-allowed disabled:opacity-60"
              disabled={streaming || !activeTable}
            />
            {streaming ? (
              <button
                onClick={() => stopRun(sessionId)}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-graphite-700 text-graphite-100 hover:bg-graphite-600 transition"
                title={t("chat.stop")}
              >
                <Icon name="stop" size={14} />
              </button>
            ) : (
              <button
                onClick={() => sendQuestion()}
                disabled={!input.trim() || !activeTable || !llmReady || clarifying}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-graphite-100 text-graphite-950 hover:bg-graphite-50 disabled:bg-graphite-700 disabled:text-graphite-500 disabled:cursor-not-allowed transition"
                title={t("chat.send")}
              >
                <Icon name="arrowUp" />
              </button>
            )}
          </div>
        </div>
        {/* Режим дашборда (#224): переключатель и генерация вариантов */}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            onClick={() => {
              if (dashboardMode) closeIdeas();
              else setDeepResearch(sessionId, false);
              setDashboardMode(!dashboardMode);
            }}
            aria-pressed={dashboardMode}
            title={t("dashboard.toggleHint")}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition ${
              dashboardMode
                ? "border-graphite-400 bg-graphite-700 text-graphite-50"
                : "border-graphite-700 text-graphite-400 hover:bg-graphite-800 hover:text-graphite-200"
            }`}
          >
            <Icon name="layout" size={13} />
            {t("dashboard.toggle")}
          </button>
          {/* Режим «Проверка» (A-29): с дашбордом не совмещается */}
          <button
            onClick={() => setVerifyMode(!verifyMode)}
            disabled={dashboardMode}
            aria-pressed={verifyMode && !dashboardMode}
            title={dashboardMode ? t("verify.dashboardHint") : t("verify.toggleHint")}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition disabled:cursor-not-allowed disabled:opacity-40 ${
              verifyMode && !dashboardMode
                ? "border-graphite-400 bg-graphite-700 text-graphite-50"
                : "border-graphite-700 text-graphite-400 hover:bg-graphite-800 hover:text-graphite-200"
            }`}
          >
            <Icon name="check" size={13} />
            {t("verify.toggle")}
          </button>
          {/* Глубокое исследование (A-28): включение выключает режим дашборда */}
          <button
            onClick={() => {
              if (!deepMode) {
                setDashboardMode(false);
                closeIdeas();
              }
              setDeepResearch(sessionId, !deepMode);
            }}
            aria-pressed={deepMode}
            title={t("research.toggleHint")}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition ${
              deepMode
                ? "border-graphite-400 bg-graphite-700 text-graphite-50"
                : "border-graphite-700 text-graphite-400 hover:bg-graphite-800 hover:text-graphite-200"
            }`}
          >
            <Icon name="search" size={13} />
            {t("research.toggle")}
          </button>
          {dashboardMode && (
            <button
              onClick={loadIdeas}
              disabled={ideas?.status === "loading" || streaming || !activeTable}
              className="inline-flex items-center gap-1.5 rounded-full border border-graphite-700 px-3 py-1 text-xs text-graphite-200 hover:bg-graphite-800 disabled:opacity-50 disabled:cursor-not-allowed transition"
            >
              <Icon
                name={ideas?.status === "loading" ? "refresh" : "sparkle"}
                size={13}
                className={ideas?.status === "loading" ? "animate-spin" : ""}
              />
              {t("dashboard.autoGenerate")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
