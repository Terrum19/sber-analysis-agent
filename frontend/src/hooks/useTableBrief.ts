"use client";

/**
 * Свежая сводка активной таблицы с бэкенда (GET /api/datasources/tables/{table}).
 * Описание и название от LLM строятся в фоне после подключения, поэтому пока
 * summary нет, сводка перезапрашивается каждые POLL_MS, не больше POLL_ATTEMPTS раз
 * (5 мин): qwen3.8-27b с reasoning строит описание около 2 мин, локальная модель
 * на CPU — дольше.
 * LLM недоступна — опрос просто заканчивается, карточка остаётся без описания.
 */
import { useEffect } from "react";
import { getTableBrief } from "@/api/client";
import { useSessionStore } from "@/store/sessionStore";

const POLL_MS = 5000;
const POLL_ATTEMPTS = 60;

export function useTableBrief(databaseId: string | undefined, table: string | undefined) {
  const updateBrief = useSessionStore((s) => s.updateBrief);

  useEffect(() => {
    if (!databaseId || !table) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;

    async function refresh() {
      attempts += 1;
      try {
        const brief = await getTableBrief(table!);
        if (cancelled) return;
        updateBrief(databaseId!, brief);
        if (brief.summary) return;
      } catch (err: any) {
        if (cancelled || err?.status === 404) return;
      }
      if (attempts < POLL_ATTEMPTS) timer = setTimeout(refresh, POLL_MS);
    }

    refresh();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [databaseId, table, updateBrief]);
}
