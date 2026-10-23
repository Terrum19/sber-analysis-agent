"use client";

/**
 * Открыть пустое окно новой сессии внутри базы данных.
 * В список сессий базы она попадёт при первом вопросе (registerSession).
 * Требует обязательного выбора таблицы (автовыбор при 1 таблице, модалка при > 1).
 */
import { useRouter } from "next/navigation";
import { createSession, setSessionTable } from "@/api/client";
import { useSessionStore, type DatabaseFolder } from "@/store/sessionStore";
import { useUiStore } from "@/store/uiStore";

export function useStartSession() {
  const router = useRouter();
  const bindSession = useSessionStore((s) => s.bindSession);
  const switchSessionTable = useSessionStore((s) => s.switchSessionTable);
  const setTableModalTarget = useUiStore((s) => s.setTableModalTarget);

  async function startSession(database: DatabaseFolder, preferredTable?: string) {
    if (preferredTable) {
      const { session_id } = await createSession(preferredTable, database.id);
      bindSession(session_id, database.id, preferredTable);
      router.push(`/session/${session_id}`);
      return;
    }

    const tables = database.tables || [];
    if (tables.length === 1) {
      const table = tables[0];
      const { session_id } = await createSession(table, database.id);
      bindSession(session_id, database.id, table);
      router.push(`/session/${session_id}`);
      return;
    }

    if (tables.length > 1) {
      setTableModalTarget(database);
      return;
    }

    // Если таблиц нет, создать сессию без таблицы нельзя
    return;
  }

  async function workWithTable(database: DatabaseFolder, table: string, sessionId?: string) {
    if (sessionId) {
      await setSessionTable(sessionId, table);
      switchSessionTable(sessionId, table);
      bindSession(sessionId, database.id, table);
    } else {
      await startSession(database, table);
    }
  }

  startSession.workWithTable = workWithTable;
  return startSession;
}

export function useWorkWithTable() {
  const router = useRouter();
  const bindSession = useSessionStore((s) => s.bindSession);
  const switchSessionTable = useSessionStore((s) => s.switchSessionTable);

  return async function workWithTable(database: DatabaseFolder, table: string, sessionId?: string) {
    if (sessionId) {
      await setSessionTable(sessionId, table);
      switchSessionTable(sessionId, table);
      bindSession(sessionId, database.id, table);
    } else {
      const { session_id } = await createSession(table, database.id);
      bindSession(session_id, database.id, table);
      router.push(`/session/${session_id}`);
    }
  };
}

export async function workWithTable(database: DatabaseFolder, table: string, sessionId?: string) {
  if (sessionId) {
    await setSessionTable(sessionId, table);
    useSessionStore.getState().switchSessionTable(sessionId, table);
    useSessionStore.getState().bindSession(sessionId, database.id, table);
  } else {
    const { session_id } = await createSession(table, database.id);
    useSessionStore.getState().bindSession(session_id, database.id, table);
    if (typeof window !== "undefined") {
      window.location.href = `/session/${session_id}`;
    }
  }
}
