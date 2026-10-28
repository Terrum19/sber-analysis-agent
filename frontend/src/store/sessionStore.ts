"use client";

/**
 * Zustand-стор сессии: сообщения, готовые графики, дашборды, выбранный элемент
 * канваса, подключённый источник данных и загруженные базы данных с их сессиями
 * (базы данных — «папки», внутри которых создаются сессии).
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { DashboardTile } from "@/types/dashboard";
import type {
  ChartEvent,
  ClarificationEvent,
  DashboardEvent,
  RefusalEvent,
  TurnProgress,
} from "@/types/chat";
import type { Datasource, SessionResponse, TableBrief } from "@/types/datasource";
import { useCanvasStore } from "@/store/canvasStore";

export interface ChatMessage {
  id?: string;
  role: "user" | "agent";
  content: string;
  /** turn_id хода, к которому относится ответ (из события done). */
  turn_id?: string;
  /** Цитата, на которую отвечает пользователь (для ленты и повтора запроса). */
  reply_to?: { turn_id: string; excerpt: string };
  dashboard?: DashboardEvent["data"];
  chart?: ChartEvent["data"];
  timestamp?: string;
  /** Сообщение об ошибке или обрыве — под ним кнопка повтора. */
  failed?: boolean;
  /** Карточка уточнения (clarification_needed). */
  clarification?: ClarificationEvent["data"];
  /** Отказ агента (refusal) с переформулировками. */
  refusal?: RefusalEvent["data"];
  /** Прогресс хода по событиям progress. */
  progress?: TurnProgress;
  /** Исходный текст вопроса, вызвавший ошибку (для кнопки повтора). */
  failedQuestion?: string;
  /** Подсказки для уточнения/повтора (если backend прислал clarification_needed). */
  suggestions?: string[];
  /** Что показали пользователю — для экспорта ответа (F-10). */
  answer?: {
    headline?: string;
    details?: string[];
    spec?: Record<string, unknown>;
    result_table?: string;
  };
}

export interface SessionItem {
  id: string;
  title: string;
  created_at?: string | number;
  pinned?: boolean;
}

export interface DatabaseFolder {
  id: string;
  /** Название в сайдбаре: пользователя, таблицы-единственной (display_name) или fileName. */
  title: string;
  /** Имена файлов или базы при подключении: подпись в тултипе. */
  fileName?: string;
  /** Пользователь переименовал базу: название таблицы его больше не заменяет. */
  renamed?: boolean;
  kind: Datasource["kind"] | "duckdb";
  tables: string[];
  /** Одно предложение о том, что за данные (profile_summary бэкенда). */
  description?: string;
  /** Паспорта таблиц из ответа подключения. */
  profiles?: TableBrief[];
  sessions: SessionItem[];
  /** Демо-база без настоящего datasource на бэкенде. */
  demo?: boolean;
  pinned?: boolean;
}

export interface SessionSnapshot {
  messages: ChatMessage[];
  tiles: DashboardTile[];
}

export interface SessionTableSnapshot {
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
  /** Активная таблица для каждой сессии. */
  sessionActiveTable: Record<string, string>;
  /** Сообщения и плитки сессий, открытых в этой вкладке. */
  sessionCache: Record<string, SessionSnapshot>;
  /** Кеш сообщений и плиток сессии, изолированный по таблицам. */
  sessionTableCache: Record<string, Record<string, SessionTableSnapshot>>;
  /** ID сессии, в которой прямо сейчас генерируется ответ агента. */
  activeStreamingSessionId: string | null;
  /**
   * Вопросы, поставленные извне (клик по графику / «Дообследовать»): ChatPanel
   * забирает их и отправляет сам, когда сессия готова. В localStorage не пишутся.
   */
  pendingQuestions: Record<string, { id: string; text: string } | undefined>;
  setStreamingSessionId: (sessionId: string | null) => void;
  /** Поставить вопрос в очередь сессии (F-09). */
  queueQuestion: (sessionId: string, text: string) => void;
  /** Убрать вопрос из очереди после отправки. */
  consumeQuestion: (sessionId: string, id: string) => void;

  setSession: (id: string) => void;
  addMessage: (msg: ChatMessage) => void;
  appendToLastMessage: (chunk: string) => void;
  updateLastMessage: (patch: Partial<ChatMessage>) => void;
  updateMessage: (id: string, patch: Partial<ChatMessage>) => void;
  removeMessage: (id: string) => void;
  removeLastFailedMessage: () => void;
  addTile: (tile: DashboardTile) => void;
  setTiles: (tiles: DashboardTile[]) => void;
  selectTile: (index: number | null) => void;
  setDatasource: (datasource: Datasource | null) => void;
  setDatasourceTitle: (title: string | null) => void;
  resetSession: () => void;

  addDatabase: (datasource: Datasource, title: string) => DatabaseFolder;
  toggleDatabase: (databaseId: string) => void;
  renameDatabase: (databaseId: string, title: string) => void;
  /** Новая сводка таблицы с бэкенда; у базы из одной таблицы — и её название. */
  updateBrief: (databaseId: string, brief: TableBrief) => void;
  togglePinDatabase: (databaseId: string) => void;
  deleteDatabase: (databaseId: string) => string[];
  bindSession: (sessionId: string, databaseId: string, initialTable?: string) => void;
  switchSessionTable: (sessionId: string, nextTable: string) => void;
  /** Добавляет сессию в папку базы данных при первом вопросе. */
  registerSession: (sessionId: string, title: string) => void;
  renameSession: (sessionId: string, title: string) => void;
  togglePinSession: (sessionId: string) => void;
  deleteSession: (sessionId: string) => void;
  /** Подтягивает сессии бэкенда в локальные папки (создаёт папки при необходимости). */
  mergeSessions: (remoteSessions: SessionResponse[]) => void;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      sessionId: null,
      messages: [],
      tiles: [],
      selectedTile: null,
      datasource: null,
      datasourceTitle: null,

      databases: [],
      collapsedDatabases: {},
      sessionDatabase: {},
      sessionActiveTable: {},
      sessionCache: {},
      sessionTableCache: {},
      activeStreamingSessionId: null,
      pendingQuestions: {},

      setStreamingSessionId: (sessionId) => set({ activeStreamingSessionId: sessionId }),

      queueQuestion: (sessionId, text) =>
        set((s) => ({
          pendingQuestions: {
            ...s.pendingQuestions,
            [sessionId]: {
              id: `q-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
              text,
            },
          },
        })),

      consumeQuestion: (sessionId, id) =>
        set((s) => {
          if (s.pendingQuestions[sessionId]?.id !== id) return {};
          const nextPending = { ...s.pendingQuestions };
          delete nextPending[sessionId];
          return { pendingQuestions: nextPending };
        }),

      setSession: (id) =>
        set((s) => {
          if (s.sessionId === id) return {};
          const cache = { ...s.sessionCache };
          const tableCache = { ...s.sessionTableCache };
          if (s.sessionId) {
            cache[s.sessionId] = { messages: s.messages, tiles: s.tiles };
            const prevTable = s.sessionActiveTable[s.sessionId];
            if (prevTable) {
              tableCache[s.sessionId] = {
                ...(tableCache[s.sessionId] || {}),
                [prevTable]: { messages: s.messages, tiles: s.tiles },
              };
            }
          }
          const activeTable = s.sessionActiveTable[id];
          let next: SessionSnapshot = { messages: [], tiles: [] };
          if (activeTable && tableCache[id]?.[activeTable]) {
            next = tableCache[id][activeTable];
          } else if (cache[id]) {
            next = cache[id];
          }
          useCanvasStore.getState().setDashboard(null, "", next.tiles);
          return {
            sessionId: id,
            sessionCache: cache,
            sessionTableCache: tableCache,
            messages: next.messages,
            tiles: next.tiles,
            selectedTile: null,
          };
        }),
      addMessage: (msg) =>
        set((s) => {
          const newMsg: ChatMessage = {
            ...msg,
            id: msg.id || `msg-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            timestamp: msg.timestamp || new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }),
          };
          const nextMessages = [...s.messages, newMsg];
          let nextTableCache = s.sessionTableCache;
          if (s.sessionId && s.sessionActiveTable[s.sessionId]) {
            const curTable = s.sessionActiveTable[s.sessionId];
            nextTableCache = {
              ...s.sessionTableCache,
              [s.sessionId]: {
                ...(s.sessionTableCache[s.sessionId] || {}),
                [curTable]: { messages: nextMessages, tiles: s.tiles },
              },
            };
          }
          return { messages: nextMessages, sessionTableCache: nextTableCache };
        }),
      removeMessage: (id) =>
        set((s) => {
          const nextMessages = s.messages.filter((m) => m.id !== id);
          let nextTableCache = s.sessionTableCache;
          if (s.sessionId && s.sessionActiveTable[s.sessionId]) {
            const curTable = s.sessionActiveTable[s.sessionId];
            nextTableCache = {
              ...s.sessionTableCache,
              [s.sessionId]: {
                ...(s.sessionTableCache[s.sessionId] || {}),
                [curTable]: { messages: nextMessages, tiles: s.tiles },
              },
            };
          }
          return { messages: nextMessages, sessionTableCache: nextTableCache };
        }),
      removeLastFailedMessage: () =>
        set((s) => {
          const lastIdx = s.messages.length - 1;
          if (lastIdx < 0 || !s.messages[lastIdx].failed) return {};
          const nextMessages = s.messages.slice(0, lastIdx);
          let nextTableCache = s.sessionTableCache;
          if (s.sessionId && s.sessionActiveTable[s.sessionId]) {
            const curTable = s.sessionActiveTable[s.sessionId];
            nextTableCache = {
              ...s.sessionTableCache,
              [s.sessionId]: {
                ...(s.sessionTableCache[s.sessionId] || {}),
                [curTable]: { messages: nextMessages, tiles: s.tiles },
              },
            };
          }
          return { messages: nextMessages, sessionTableCache: nextTableCache };
        }),
      appendToLastMessage: (chunk) =>
        set((s) => {
          const last = s.messages[s.messages.length - 1];
          if (!last) return {};
          const nextMessages = [
            ...s.messages.slice(0, -1),
            { ...last, content: last.content + chunk },
          ];
          let nextTableCache = s.sessionTableCache;
          if (s.sessionId && s.sessionActiveTable[s.sessionId]) {
            const curTable = s.sessionActiveTable[s.sessionId];
            nextTableCache = {
              ...s.sessionTableCache,
              [s.sessionId]: {
                ...(s.sessionTableCache[s.sessionId] || {}),
                [curTable]: { messages: nextMessages, tiles: s.tiles },
              },
            };
          }
          return { messages: nextMessages, sessionTableCache: nextTableCache };
        }),
      updateMessage: (id, patch) =>
        set((s) => {
          if (!s.messages.some((m) => m.id === id)) return {};
          const nextMessages = s.messages.map((m) => (m.id === id ? { ...m, ...patch } : m));
          let nextTableCache = s.sessionTableCache;
          if (s.sessionId && s.sessionActiveTable[s.sessionId]) {
            const curTable = s.sessionActiveTable[s.sessionId];
            nextTableCache = {
              ...s.sessionTableCache,
              [s.sessionId]: {
                ...(s.sessionTableCache[s.sessionId] || {}),
                [curTable]: { messages: nextMessages, tiles: s.tiles },
              },
            };
          }
          return { messages: nextMessages, sessionTableCache: nextTableCache };
        }),
      updateLastMessage: (patch) =>
        set((s) => {
          const last = s.messages[s.messages.length - 1];
          if (!last) return {};
          const nextMessages = [
            ...s.messages.slice(0, -1),
            { ...last, ...patch },
          ];
          let nextTableCache = s.sessionTableCache;
          if (s.sessionId && s.sessionActiveTable[s.sessionId]) {
            const curTable = s.sessionActiveTable[s.sessionId];
            nextTableCache = {
              ...s.sessionTableCache,
              [s.sessionId]: {
                ...(s.sessionTableCache[s.sessionId] || {}),
                [curTable]: { messages: nextMessages, tiles: s.tiles },
              },
            };
          }
          return { messages: nextMessages, sessionTableCache: nextTableCache };
        }),
      addTile: (tile) => {
        if (!useCanvasStore.getState().dashboardId) {
          useCanvasStore.getState().addTile(tile);
        }
        set((s) => {
          const nextTiles = [...s.tiles, tile];
          let nextTableCache = s.sessionTableCache;
          if (s.sessionId && s.sessionActiveTable[s.sessionId]) {
            const curTable = s.sessionActiveTable[s.sessionId];
            nextTableCache = {
              ...s.sessionTableCache,
              [s.sessionId]: {
                ...(s.sessionTableCache[s.sessionId] || {}),
                [curTable]: { messages: s.messages, tiles: nextTiles },
              },
            };
          }
          return { tiles: nextTiles, sessionTableCache: nextTableCache };
        });
      },
      setTiles: (tiles) => {
        if (!useCanvasStore.getState().dashboardId) {
          useCanvasStore.getState().setTiles(tiles);
        }
        set((s) => {
          let nextTableCache = s.sessionTableCache;
          if (s.sessionId && s.sessionActiveTable[s.sessionId]) {
            const curTable = s.sessionActiveTable[s.sessionId];
            nextTableCache = {
              ...s.sessionTableCache,
              [s.sessionId]: {
                ...(s.sessionTableCache[s.sessionId] || {}),
                [curTable]: { messages: s.messages, tiles },
              },
            };
          }
          return { tiles, sessionTableCache: nextTableCache };
        });
      },
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
          activeStreamingSessionId: null,
        }),

      addDatabase: (datasource, title) => {
        const single = datasource.tables.length === 1 ? datasource.profiles?.[0]?.display_name : null;
        const folder: DatabaseFolder = {
          id: datasource.datasource_id,
          title: single || title,
          fileName: title,
          kind: datasource.kind,
          tables: datasource.tables,
          description: datasource.profile_summary,
          profiles: datasource.profiles,
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
          databases: s.databases.map((d) =>
            d.id === databaseId && title.trim() ? { ...d, title: title.trim(), renamed: true } : d
          ),
        })),
      updateBrief: (databaseId, brief) =>
        set((s) => ({
          databases: s.databases.map((d) => {
            if (d.id !== databaseId) return d;
            const profiles = d.profiles ?? [];
            const known = profiles.some((x) => x.table === brief.table);
            const named = !d.renamed && d.tables.length === 1 && d.tables[0] === brief.table && brief.display_name;
            return {
              ...d,
              profiles: known ? profiles.map((x) => (x.table === brief.table ? brief : x)) : [...profiles, brief],
              ...(named ? { title: brief.display_name! } : {}),
            };
          }),
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
          const nextActiveTable = { ...s.sessionActiveTable };
          const nextTableCache = { ...s.sessionTableCache };
          const nextPending = { ...s.pendingQuestions };
          for (const sid of sessionIds) {
            delete nextSessionDb[sid];
            delete nextCache[sid];
            delete nextActiveTable[sid];
            delete nextTableCache[sid];
            delete nextPending[sid];
          }
          const isActiveDeleted = s.sessionId ? sessionIds.includes(s.sessionId) : false;
          const isStreamingDeleted = s.activeStreamingSessionId ? sessionIds.includes(s.activeStreamingSessionId) : false;
          const isDatasourceDeleted = s.datasource?.datasource_id === databaseId;
          return {
            databases: nextDatabases,
            collapsedDatabases: nextCollapsed,
            sessionDatabase: nextSessionDb,
            sessionCache: nextCache,
            sessionActiveTable: nextActiveTable,
            sessionTableCache: nextTableCache,
            pendingQuestions: nextPending,
            ...(isActiveDeleted ? { sessionId: null, messages: [], tiles: [], selectedTile: null } : {}),
            ...(isStreamingDeleted ? { activeStreamingSessionId: null } : {}),
            ...(isDatasourceDeleted ? { datasource: null, datasourceTitle: null } : {}),
          };
        });
        return sessionIds;
      },
      bindSession: (sessionId, databaseId, initialTable) =>
        set((s) => {
          const nextActiveTable = initialTable
            ? { ...s.sessionActiveTable, [sessionId]: initialTable }
            : s.sessionActiveTable;
          const nextSessionDb = { ...s.sessionDatabase, [sessionId]: databaseId };
          let nextMessages = s.messages;
          let nextTiles = s.tiles;
          if (s.sessionId === sessionId && initialTable && s.messages.length === 0) {
            const cached = s.sessionTableCache[sessionId]?.[initialTable];
            if (cached) {
              nextMessages = cached.messages;
              nextTiles = cached.tiles;
              useCanvasStore.getState().setDashboard(null, "", nextTiles);
            }
          }
          return {
            sessionDatabase: nextSessionDb,
            sessionActiveTable: nextActiveTable,
            messages: nextMessages,
            tiles: nextTiles,
          };
        }),
      switchSessionTable: (sessionId, nextTable) => {
        const state = get();
        const currentTable = state.sessionActiveTable[sessionId];
        const sessionTableCache = { ...state.sessionTableCache };
        const currentSessionTableCache = { ...(sessionTableCache[sessionId] || {}) };

        if (currentTable) {
          currentSessionTableCache[currentTable] = {
            messages: state.messages,
            tiles: state.tiles,
          };
        }
        sessionTableCache[sessionId] = currentSessionTableCache;

        const nextSnapshot = currentSessionTableCache[nextTable] || { messages: [], tiles: [] };
        const sessionCache = {
          ...state.sessionCache,
          [sessionId]: { messages: nextSnapshot.messages, tiles: nextSnapshot.tiles },
        };

        useCanvasStore.getState().setDashboard(null, "", nextSnapshot.tiles);

        set({
          sessionActiveTable: { ...state.sessionActiveTable, [sessionId]: nextTable },
          sessionTableCache,
          sessionCache,
          messages: nextSnapshot.messages,
          tiles: nextSnapshot.tiles,
          selectedTile: null,
        });
      },
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
          const nextActiveTable = { ...s.sessionActiveTable };
          delete nextActiveTable[sessionId];
          const nextTableCache = { ...s.sessionTableCache };
          delete nextTableCache[sessionId];
          const nextPending = { ...s.pendingQuestions };
          delete nextPending[sessionId];
          const isActiveDeleted = s.sessionId === sessionId;
          const isStreamingDeleted = s.activeStreamingSessionId === sessionId;
          return {
            databases: nextDatabases,
            sessionDatabase: nextSessionDb,
            sessionCache: nextCache,
            sessionActiveTable: nextActiveTable,
            sessionTableCache: nextTableCache,
            pendingQuestions: nextPending,
            ...(isActiveDeleted ? { sessionId: null, messages: [], tiles: [], selectedTile: null } : {}),
            ...(isStreamingDeleted ? { activeStreamingSessionId: null } : {}),
          };
        }),
      mergeSessions: (remoteSessions: SessionResponse[]) =>
        set((s) => {
          const nextDatabases = s.databases.map((d) => ({
            ...d,
            tables: [...d.tables],
            sessions: [...d.sessions],
          }));
          const nextSessionDb = { ...s.sessionDatabase };
          const nextActiveTable = { ...s.sessionActiveTable };

          for (const remote of remoteSessions) {
            if (!remote.session_id) continue;

            // 1. Map by datasource_id or matching table
            let db = nextDatabases.find((d) => remote.datasource_id && d.id === remote.datasource_id);
            if (!db && remote.table) {
              db = nextDatabases.find((d) => d.tables.includes(remote.table!));
            }

            // If no existing folder matches, create a folder for the table/datasource
            if (!db && remote.table) {
              db = {
                id: remote.datasource_id || `ds-${remote.table}`,
                title: remote.table,
                kind: "duckdb",
                tables: [remote.table],
                sessions: [],
              };
              nextDatabases.push(db);
            }

            if (db) {
              nextSessionDb[remote.session_id] = db.id;
              const sessionExists = db.sessions.some((x) => x.id === remote.session_id);
              if (!sessionExists) {
                db.sessions = [
                  {
                    id: remote.session_id,
                    title: remote.title || remote.table || "Сессия",
                    created_at: remote.created_at ? new Date(remote.created_at).getTime() : Date.now(),
                  },
                  ...db.sessions,
                ];
              } else if (remote.title) {
                // Название на бэкенде — источник правды (переименование, #147).
                db.sessions = db.sessions.map((x) =>
                  x.id === remote.session_id ? { ...x, title: remote.title || x.title } : x
                );
              }
            }

            if (remote.table && !nextActiveTable[remote.session_id]) {
              nextActiveTable[remote.session_id] = remote.table;
            }
          }

          return {
            databases: nextDatabases,
            sessionDatabase: nextSessionDb,
            sessionActiveTable: nextActiveTable,
          };
        }),
    }),
    {
      name: "sber_databases",
      storage: createJSONStorage(() => (typeof window !== "undefined" ? localStorage : ({} as any))),
      partialize: (state) => ({
        databases: state.databases.filter((d) => !d.demo && d.id !== "demo-sales-passport"),
        collapsedDatabases: state.collapsedDatabases,
        sessionDatabase: state.sessionDatabase,
        sessionActiveTable: state.sessionActiveTable,
        sessionTableCache: state.sessionTableCache,
      }),
      onRehydrateStorage: () => (state) => {
        if (state) {
          if (state.databases?.some((d) => d.demo || d.id === "demo-sales-passport")) {
            state.databases = state.databases.filter((d) => !d.demo && d.id !== "demo-sales-passport");
          }
          if (state.sessionDatabase) {
            delete state.sessionDatabase["demo-sales"];
            delete state.sessionDatabase["demo-kpi"];
          }
          if (state.sessionActiveTable) {
            delete state.sessionActiveTable["demo-sales"];
            delete state.sessionActiveTable["demo-kpi"];
          }
          if (state.sessionTableCache) {
            delete state.sessionTableCache["demo-sales"];
            delete state.sessionTableCache["demo-kpi"];
          }
        }
      },
    }
  )
);

/** Название таблицы для пользователя: display_name из сводки или имя таблицы в DuckDB. */
export function tableTitle(database: DatabaseFolder | null | undefined, table: string): string {
  return database?.profiles?.find((brief) => brief.table === table)?.display_name || table;
}

/** База данных, к которой относится сессия. */
export function useSessionDatabase(sessionId: string | null | undefined): DatabaseFolder | null {
  return useSessionStore((s) => {
    const databaseId = sessionId ? s.sessionDatabase[sessionId] : undefined;
    return (databaseId && s.databases.find((d) => d.id === databaseId)) || null;
  });
}
