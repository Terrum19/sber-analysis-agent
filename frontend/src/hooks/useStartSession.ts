"use client";

/**
 * Открыть пустое окно новой сессии внутри базы данных.
 * В список сессий базы она попадёт при первом вопросе (registerSession).
 */
import { useRouter } from "next/navigation";
import { createSession } from "@/api/client";
import { useSessionStore, type DatabaseFolder } from "@/store/sessionStore";

export function useStartSession() {
  const router = useRouter();
  const bindSession = useSessionStore((s) => s.bindSession);

  return async function startSession(database: DatabaseFolder) {
    const { session_id } = await createSession(database.demo ? undefined : database.id);
    bindSession(session_id, database.id);
    router.push(`/session/${session_id}`);
  };
}
