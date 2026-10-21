"use client";

/**
 * Открыть пустое окно новой сессии внутри базы данных.
 * В список сессий базы она попадёт при первом вопросе (registerSession).
 * Требует обязательного выбора таблицы (автовыбор при 1 таблице, модалка при > 1).
 */
import { useRouter } from "next/navigation";
import { createSession } from "@/api/client";
import { useSessionStore, type DatabaseFolder } from "@/store/sessionStore";
import { useUiStore } from "@/store/uiStore";

export function useStartSession() {
  const router = useRouter();
  const bindSession = useSessionStore((s) => s.bindSession);
  const setTableModalTarget = useUiStore((s) => s.setTableModalTarget);

  return async function startSession(database: DatabaseFolder, preferredTable?: string) {
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
  };
}
