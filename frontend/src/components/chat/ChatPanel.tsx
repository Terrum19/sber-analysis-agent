"use client";

/**
 * План-минимум: чат, в который LLM стримит текст, а картинки/дашборды
 * прилетают в момент готовности (SSE). План-максимум: элементы уходят
 * на канвас справа.
 */
import { useEffect, useRef, useState } from "react";
import { RECOVERY_SKIP_STEP, streamChat } from "@/api/client";
import { useSessionStore } from "@/store/sessionStore";
import DatasourcePanel from "@/components/chat/DatasourcePanel";

// Подсказки clarification_needed (orchestrator.CLARIFICATION_SUGGESTIONS).
const SKIP_STEP = "Пропустить этот шаг и продолжить";
const REPHRASE = "Переформулировать вопрос";

/** Агент ждёт ответа на уточнение после сбоя шага. */
interface Clarification {
  suggestions: string[];
  // Следующее сообщение пользователя уходит как recovery_response.
  awaitingText: boolean;
}

export default function ChatPanel({ sessionId }: { sessionId: string }) {
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [clarification, setClarification] = useState<Clarification | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const messages = useSessionStore((s) => s.messages);
  const addMessage = useSessionStore((s) => s.addMessage);
  const appendToLastMessage = useSessionStore((s) => s.appendToLastMessage);
  const addTile = useSessionStore((s) => s.addTile);
  // Отмена текущего стрима: только по кнопке «Стоп» или при размонтировании.
  const stopRef = useRef<(() => void) | null>(null);

  useEffect(() => () => stopRef.current?.(), []);

  function send() {
    if (!input.trim() || streaming) return;
    const question = input;
    setInput("");
    // Текст после «Уточнить колонки» — уточнение к упавшему шагу, иначе новый вопрос
    // (бэкенд тогда сам забудет несостоявшееся уточнение).
    ask(question, clarification?.awaitingText ? question : undefined);
  }

  function chooseSuggestion(suggestion: string) {
    if (streaming) return;
    if (suggestion === SKIP_STEP) {
      ask(suggestion, RECOVERY_SKIP_STEP);
    } else if (suggestion === REPHRASE) {
      setClarification(null);
      inputRef.current?.focus();
    } else {
      setClarification({ suggestions: [], awaitingText: true });
      inputRef.current?.focus();
    }
  }

  // При recoveryResponse бэкенд отвечает на исходный вопрос из сохранённого контекста.
  function ask(question: string, recoveryResponse?: string) {
    addMessage({ role: "user", content: question });
    setClarification(null);
    setStreaming(true);

    // Текстовые чанки одного ответа копятся в одном сообщении агента.
    let answerStarted = false;
    const appendText = (text: string) => {
      if (answerStarted) {
        appendToLastMessage(text);
      } else {
        answerStarted = true;
        addMessage({ role: "agent", content: text });
      }
    };

    stopRef.current = streamChat(
      sessionId,
      question,
      (event) => {
        if (event.type === "chart") {
          addTile({ title: event.data.caption, spec: event.data.spec, layout: { x: 0, y: 0, w: 6, h: 4 } });
          answerStarted = false;
        } else if (event.type === "text") {
          const content = event.data?.headline ?? event.data?.chunk ?? "";
          if (content) appendText(content);
        } else if (event.type === "clarification_needed") {
          addMessage({
            role: "agent",
            content: `Не получилось выполнить шаг: ${event.data.error}\nКак продолжить?`,
          });
          setClarification({ suggestions: event.data.suggestions ?? [], awaitingText: false });
          answerStarted = false;
        } else if (event.type === "error") {
          const detail = event.data?.message ?? event.data?.detail;
          addMessage({ role: "agent", content: `Ошибка: ${typeof detail === "string" ? detail : "агент не смог ответить"}` });
          answerStarted = false;
        }
      },
      (error) => {
        if (error) addMessage({ role: "agent", content: `Соединение прервано: ${error}` });
        stopRef.current = null;
        setStreaming(false);
      },
      recoveryResponse,
    );
  }

  function stop() {
    stopRef.current?.();
  }

  return (
    <div className="flex h-full flex-col p-4">
      <DatasourcePanel />
      <div className="flex-1 space-y-3 overflow-y-auto">
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-right" : "text-left"}>
            <span className={m.role === "user" ? "inline-block rounded-lg bg-blue-100 px-3 py-2" : "inline-block whitespace-pre-wrap rounded-lg bg-slate-100 px-3 py-2"}>
              {m.content}
            </span>
          </div>
        ))}
        {clarification && clarification.suggestions.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {clarification.suggestions.map((suggestion) => (
              <button
                key={suggestion}
                onClick={() => chooseSuggestion(suggestion)}
                disabled={streaming}
                className="rounded-lg border border-blue-300 px-3 py-1 text-sm text-blue-700 hover:bg-blue-50 disabled:opacity-50"
              >
                {suggestion}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="mt-4 flex gap-2">
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder={
            clarification?.awaitingText
              ? "Уточните, какие колонки использовать..."
              : "Спросите что-угодно о данных..."
          }
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2"
        />
        {streaming ? (
          <button onClick={stop} className="rounded-lg bg-slate-600 px-4 py-2 text-white">
            Стоп
          </button>
        ) : (
          <button onClick={send} className="rounded-lg bg-blue-600 px-4 py-2 text-white">
            Отправить
          </button>
        )}
      </div>
    </div>
  );
}
