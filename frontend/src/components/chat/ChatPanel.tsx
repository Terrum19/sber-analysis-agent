"use client";

/**
 * План-минимум: чат, в который LLM стримит текст, а картинки/дашборды
 * прилетают в момент готовности (SSE). План-максимум: элементы уходят
 * на канвас справа.
 */
import { useState } from "react";
import { streamChat } from "@/api/client";
import { useSessionStore } from "@/store/sessionStore";

export default function ChatPanel({ sessionId }: { sessionId: string }) {
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const { addMessage, addTile } = useSessionStore();

  async function send() {
    if (!input.trim()) return;
    addMessage({ role: "user", content: input });
    const question = input;
    setInput("");
    setStreaming(true);

    const stop = streamChat(sessionId, question, (event) => {
      if (event.type === "chart") {
        addTile({ title: event.data.caption, spec: event.data.spec, layout: { x: 0, y: 0, w: 6, h: 4 } });
      } else if (event.type === "text") {
        const content = event.data.headline ?? event.data.chunk ?? "";
        if (content) addMessage({ role: "agent", content });
      }
    });

    await Promise.resolve().then(() => stop());
    setStreaming(false);
  }

  return (
    <div className="flex h-full flex-col p-4">
      <div className="flex-1 space-y-3 overflow-y-auto">
        {useSessionStore.getState().messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-right" : "text-left"}>
            <span className={m.role === "user" ? "inline-block rounded-lg bg-blue-100 px-3 py-2" : "inline-block rounded-lg bg-slate-100 px-3 py-2"}>
              {m.content}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-4 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Спросите что-угодно о данных..."
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2"
        />
        <button onClick={send} disabled={streaming} className="rounded-lg bg-blue-600 px-4 py-2 text-white disabled:opacity-50">
          Отправить
        </button>
      </div>
    </div>
  );
}
