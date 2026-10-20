"use client";

/**
 * Zustand-стор сессии: сообщения, готовые графики, дашборды, выбранный элемент
 * канваса, подключённый источник данных и загруженные базы данных с их сессиями
 * (базы данных — «папки», внутри которых создаются сессии).
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { DashboardTile } from "@/types/dashboard";
import type { DashboardEvent, ChartEvent } from "@/types/chat";
import type { Datasource } from "@/types/datasource";

export interface ChatMessage {
  id?: string;
  role: "user" | "agent";
  content: string;
  dashboard?: DashboardEvent["data"];
  chart?: ChartEvent["data"];
  timestamp?: string;
  /** Сообщение об ошибке или обрыве — под ним кнопка повтора. */
  failed?: boolean;
}

export interface SessionItem {
  id: string;
  title: string;
  created_at?: string | number;
  pinned?: boolean;
}

export interface DatabaseFolder {
  id: string;
  title: string;
  kind: Datasource["kind"] | "duckdb";
  tables: string[];
  /** Одно предложение о том, что за данные (profile_summary бэкенда). */
  description?: string;
  sessions: SessionItem[];
  /** Демо-база без настоящего datasource на бэкенде. */
  demo?: boolean;
  pinned?: boolean;
}

interface SessionSnapshot {
  messages: ChatMessage[];
  tiles: DashboardTile[];
}

interface SessionState {
  sessionId: string | null;
  messages: ChatMessage[];
  tiles: DashboardTile[];
  selectedTile: number | null;
  datasource: Datasource | null;
  datasourceTitle: string | null;

  databases: DatabaseFolder[];
  collapsedDatabases: Record<string, boolean>;
  /** К какой базе данных относится сессия (в т.ч. ещё пустая). */
  sessionDatabase: Record<string, string>;
  /** Сообщения и плитки сессий, открытых в этой вкладке. */
  sessionCache: Record<string, SessionSnapshot>;

  setSession: (id: string) => void;
  addMessage: (msg: ChatMessage) => void;
  appendToLastMessage: (chunk: string) => void;
  updateLastMessage: (patch: Partial<ChatMessage>) => void;
  addTile: (tile: DashboardTile) => void;
  setTiles: (tiles: DashboardTile[]) => void;
  selectTile: (index: number | null) => void;
  setDatasource: (datasource: Datasource | null) => void;
  setDatasourceTitle: (title: string | null) => void;
  resetSession: () => void;

  addDatabase: (datasource: Datasource, title: string) => DatabaseFolder;
  toggleDatabase: (databaseId: string) => void;
  renameDatabase: (databaseId: string, title: string) => void;
  togglePinDatabase: (databaseId: string) => void;
  deleteDatabase: (databaseId: string) => string[];
  bindSession: (sessionId: string, databaseId: string) => void;
  /** Добавляет сессию в папку базы данных при первом вопросе. */
  registerSession: (sessionId: string, title: string) => void;
  renameSession: (sessionId: string, title: string) => void;
  togglePinSession: (sessionId: string) => void;
  deleteSession: (sessionId: string) => void;
}

const DEMO_DATABASE: DatabaseFolder = {
  id: "demo-sales-passport",
  title: "sales_passport (DuckDB)",
  kind: "duckdb",
  tables: ["sales_passport"],
  demo: true,
  sessions: [
    { id: "demo-sales", title: "Анализ выручки по регионам", created_at: Date.now() - 3600000 },
    { id: "demo-kpi", title: "Динамика среднего чека и клиентов", created_at: Date.now() - 86400000 },
  ],
};

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      sessionId: null,
      messages: [],
      tiles: [],
      selectedTile: null,
      datasource: null,
      datasourceTitle: null,

      databases: [DEMO_DATABASE],
      collapsedDatabases: {},
      sessionDatabase: {
        "demo-sales": DEMO_DATABASE.id,
        "demo-kpi": DEMO_DATABASE.id,
      },
      sessionCache: {},

      setSession: (id) =>
        set((s) => {
          if (s.sessionId === id) return {};
          const cache = { ...s.sessionCache };
          if (s.sessionId) cache[s.sessionId] = { messages: s.messages, tiles: s.tiles };
          const next = cache[id] ?? { messages: [], tiles: [] };
          return { sessionId: id, sessionCache: cache, messages: next.messages, tiles: next.tiles, selectedTile: null };
        }),
      addMessage: (msg) =>
        set((s) => ({
          messages: [
            ...s.messages,
            {
              ...msg,
              id: msg.id || `msg-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
              timestamp: msg.timestamp || new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }),
            },
          ],
        })),
      appendToLastMessage: (chunk) =>
        set((s) => {
          const last = s.messages[s.messages.length - 1];
          if (!last) return {};
          return {
            messages: [
              ...s.messages.slice(0, -1),
              { ...last, content: last.content + chunk },
            ],
          };
        }),
      updateLastMessage: (patch) =>
        set((s) => {
          const last = s.messages[s.messages.length - 1];
          if (!last) return {};
          return {
            messages: [
              ...s.messages.slice(0, -1),
              { ...last, ...patch },
            ],
          };
        }),
      addTile: (tile) => set((s) => ({ tiles: [...s.tiles, tile] })),
      setTiles: (tiles) => set({ tiles }),
      selectTile: (index) => set({ selectedTile: index }),
      setDatasource: (datasource) =>
        set({
          datasource,
          datasourceTitle: datasource?.profile_summary || (datasource?.tables?.[0] ? `Таблица: ${datasource.tables[0]}` : null),
        }),

      setDatasourceTitle: (datasourceTitle) => set({ datasourceTitle }),
      resetSession: () =>
        set({
          sessionId: null,
          messages: [],
          tiles: [],
          selectedTile: null,
          datasource: null,
        }),

      addDatabase: (datasource, title) => {
        const folder: DatabaseFolder = {
          id: datasource.datasource_id,
          title,
          kind: datasource.kind,
          tables: datasource.tables,
          description: datasource.profile_summary,
          sessions: [],
        };
        set((s) => ({
          databases: [folder, ...s.databases.filter((d) => d.id !== folder.id)],
          collapsedDatabases: { ...s.collapsedDatabases, [folder.id]: false },
        }));
        return folder;
      },
      toggleDatabase: (databaseId) =>
        set((s) => ({
          collapsedDatabases: { ...s.collapsedDatabases, [databaseId]: !s.collapsedDatabases[databaseId] },
        })),
      renameDatabase: (databaseId, title) =>
        set((s) => ({
          databases: s.databases.map((d) => (d.id === databaseId ? { ...d, title: title.trim() || d.title } : d)),
        })),
      togglePinDatabase: (databaseId) =>
        set((s) => ({
          databases: s.databases.map((d) => (d.id === databaseId ? { ...d, pinned: !d.pinned } : d)),
        })),
      deleteDatabase: (databaseId) => {
        const db = get().databases.find((d) => d.id === databaseId);
        const sessionIds = db ? db.sessions.map((s) => s.id) : [];
        set((s) => {
          const nextDatabases = s.databases.filter((d) => d.id !== databaseId);
          const nextCollapsed = { ...s.collapsedDatabases };
          delete nextCollapsed[databaseId];
          const nextSessionDb = { ...s.sessionDatabase };
          const nextCache = { ...s.sessionCache };
          for (const sid of sessionIds) {
            delete nextSessionDb[sid];
            delete nextCache[sid];
          }
          const isActiveDeleted = s.sessionId ? sessionIds.includes(s.sessionId) : false;
          const isDatasourceDeleted = s.datasource?.datasource_id === databaseId;
          return {
            databases: nextDatabases,
            collapsedDatabases: nextCollapsed,
            sessionDatabase: nextSessionDb,
            sessionCache: nextCache,
            ...(isActiveDeleted ? { sessionId: null, messages: [], tiles: [], selectedTile: null } : {}),
            ...(isDatasourceDeleted ? { datasource: null, datasourceTitle: null } : {}),
          };
        });
        return sessionIds;
      },
      bindSession: (sessionId, databaseId) =>
        set((s) => ({ sessionDatabase: { ...s.sessionDatabase, [sessionId]: databaseId } })),
      registerSession: (sessionId, title) => {
        const databaseId = get().sessionDatabase[sessionId];
        if (!databaseId) return;
        set((s) => ({
          databases: s.databases.map((d) =>
            d.id !== databaseId || d.sessions.some((x) => x.id === sessionId)
              ? d
              : { ...d, sessions: [{ id: sessionId, title, created_at: Date.now() }, ...d.sessions] },
          ),
          collapsedDatabases: { ...s.collapsedDatabases, [databaseId]: false },
        }));
      },
      renameSession: (sessionId, title) =>
        set((s) => ({
          databases: s.databases.map((d) => ({
            ...d,
            sessions: d.sessions.map((x) => (x.id === sessionId ? { ...x, title: title.trim() || x.title } : x)),
          })),
        })),
      togglePinSession: (sessionId) =>
        set((s) => ({
          databases: s.databases.map((d) => ({
            ...d,
            sessions: d.sessions.map((x) => (x.id === sessionId ? { ...x, pinned: !x.pinned } : x)),
          })),
        })),
      deleteSession: (sessionId) =>
        set((s) => {
          const nextDatabases = s.databases.map((d) => ({
            ...d,
            sessions: d.sessions.filter((x) => x.id !== sessionId),
          }));
          const nextSessionDb = { ...s.sessionDatabase };
          delete nextSessionDb[sessionId];
          const nextCache = { ...s.sessionCache };
          delete nextCache[sessionId];
          const isActiveDeleted = s.sessionId === sessionId;
          return {
            databases: nextDatabases,
            sessionDatabase: nextSessionDb,
            sessionCache: nextCache,
            ...(isActiveDeleted ? { sessionId: null, messages: [], tiles: [], selectedTile: null } : {}),
          };
        }),
    }),
    {
      name: "sber_databases",
      storage: createJSONStorage(() => (typeof window !== "undefined" ? localStorage : ({} as any))),
      partialize: (state) => ({
        databases: state.databases,
        collapsedDatabases: state.collapsedDatabases,
        sessionDatabase: state.sessionDatabase,
      }),
    }
  )
);

/** База данных, к которой относится сессия. */
export function useSessionDatabase(sessionId: string | null | undefined): DatabaseFolder | null {
  return useSessionStore((s) => {
    const databaseId = sessionId ? s.sessionDatabase[sessionId] : undefined;
    return (databaseId && s.databases.find((d) => d.id === databaseId)) || null;
  });
}
