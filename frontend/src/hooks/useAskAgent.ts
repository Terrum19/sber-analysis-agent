"use client";

/**
 * Отправляет вопрос агенту из любого места интерфейса (F-09: клик по графику
 * или пункт «Дообследовать» в меню плитки). Вопрос ставится в очередь сессии,
 * а ChatPanel отправляет его, когда домонтируется и получит таблицу сессии.
 */
import { usePathname, useRouter } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";

export function useAskAgent() {
  const router = useRouter();
  const pathname = usePathname();

  return function askAgent(sessionId: string, text: string) {
    if (!sessionId || !text.trim()) return;
    useSessionStore.getState().queueQuestion(sessionId, text);
    // На узких экранах открыта либо вкладка «Чат», либо «Дашборд» —
    // просим страницу сессии показать чат, чтобы вопрос был виден.
    if (typeof window !== "undefined") {
      window.dispatchEvent(new Event("chat:focus"));
    }
    const target = `/session/${sessionId}`;
    // С канваса мы уже в этой сессии — лишний переход не нужен.
    if (pathname !== target) router.push(target);
  };
}
