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
  DashboardFallbackEvent,
  DashboardIdea,
  RefusalEvent,
  TurnProgress,
} from "@/types/chat";
import type { Datasource, SessionResponse, TableBrief } from "@/types/datasource";
import { useCanvasStore } from "@/store/canvasStore";

export interface ChatMessage {
  id?: string;
  role: "user" | "agent";
  content: string;
  isStreaming?: boolean;
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
  /** Упавший ход был в режиме дашборда: повтор идёт с тем же idea (#224). */
  failedDashboard?: { idea: DashboardIdea | null };
  /** Своего запроса не хватило: баннер и кнопки остальных идей (#224). */
  dashboardFallback?: DashboardFallbackEvent["data"];
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

export interface SessionState {
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
  /**
   * Вопросы, поставленные извне (клик по графику / «Дообследовать»): ChatPanel
   * забирает их и отправляет сам, когда сессия готова. В localStorage не пишутся.
   */
  pendingQuestions: Record<string, { id: string; text: string } | undefined>;
  /** Поставить вопрос в очередь сессии (F-09). */
  queueQuestion: (sessionId: string, text: string) => void;
  /** Убрать вопрос из очереди после отправки. */
  consumeQuestion: (sessionId: string, id: string) => void;
  /** Названия вариантов дашборда, по которым в сессии уже строили (#224): их не предлагаем снова. */
  usedIdeas: Record<string, string[]>;
  markIdeaUsed: (sessionId: string, title: string) => void;

  setSession: (id: string) => void;
  /*
   * Лента и плитки: sessionId — сессия, в которую пишем (по умолчанию открытая).
   * Ход агента продолжается в фоне, поэтому пишет в свою сессию, где бы ни был пользователь.
   */
  addMessage: (msg: ChatMessage, sessionId?: string) => string;
  appendToLastMessage: (chunk: string, sessionId?: string) => void;
  /** Дописать текст в сообщение по id (анимация печати не зависит от порядка ленты). */
  appendToMessage: (id: string, chunk: string, sessionId?: string) => void;
  updateLastMessage: (patch: Partial<ChatMessage>, sessionId?: string) => void;
  updateMessage: (id: string, patch: Partial<ChatMessage>, sessionId?: string) => void;
  removeMessage: (id: string, sessionId?: string) => void;
  removeLastFailedMessage: (sessionId?: string) => void;
  addTile: (tile: DashboardTile, sessionId?: string) => void;
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
  /** Агент сам перешёл к таблице: лента сессии остаётся той же, меняется только таблица. */
  followAgentTable: (sessionId: string, nextTable: string) => void;
  /** Добавляет сессию в папку базы данных при первом вопросе. */
  registerSession: (sessionId: string, title: string) => void;
  renameSession: (sessionId: string, title: string) => void;
  togglePinSession: (sessionId: string) => void;
  deleteSession: (sessionId: string) => void;
  /** Обновляет паспорт таблицы (brief) внутри соответствующей базы данных. */
  updateTableBrief: (table: string, brief: TableBrief) => void;
  /** Подтягивает сессии бэкенда в локальные папки (создаёт папки при необходимости). */
  mergeSessions: (remoteSessions: SessionResponse[]) => void;
}

/** Лента и плитки сессии: открытой — из стора, фоновой — из кеша её активной таблицы. */
export function sessionSnapshot(s: SessionState, sessionId: string): SessionSnapshot {
  if (sessionId === s.sessionId) return { messages: s.messages, tiles: s.tiles };
  const table = s.sessionActiveTable[sessionId];
  return (table && s.sessionTableCache[sessionId]?.[table]) || s.sessionCache[sessionId] || { messages: [], tiles: [] };
}

/**
 * Меняет ленту и плитки сессии. Открытая сессия живёт в messages/tiles, фоновая —
 * в кешах: при возврате setSession достанет её снимок активной таблицы.
 */
function patchSession(
  s: SessionState,
  sessionId: string | undefined,
  change: (snap: SessionSnapshot) => SessionSnapshot,
): Partial<SessionState> {
  const id = sessionId ?? s.sessionId;
  const table = id ? s.sessionActiveTable[id] : undefined;
  if (!id || id === s.sessionId) {
    const next = change({ messages: s.messages, tiles: s.tiles });
    if (!id || !table) return { messages: next.messages, tiles: next.tiles };
    return {
      messages: next.messages,
      tiles: next.tiles,
      sessionTableCache: { ...s.sessionTableCache, [id]: { ...(s.sessionTableCache[id] || {}), [table]: next } },
    };
  }
  const next = change(sessionSnapshot(s, id));
  return {
    sessionCache: { ...s.sessionCache, [id]: next },
    ...(table
      ? { sessionTableCache: { ...s.sessionTableCache, [id]: { ...(s.sessionTableCache[id] || {}), [table]: next } } }
      : {}),
  };
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
      pendingQuestions: {},
      usedIdeas: {},

      markIdeaUsed: (sessionId, title) =>
        set((s) => {
          const used = s.usedIdeas[sessionId] ?? [];
          if (used.includes(title)) return {};
          return { usedIdeas: { ...s.usedIdeas, [sessionId]: [...used, title] } };
        }),

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
      addMessage: (msg, sessionId) => {
        const assignedId = msg.id || `msg-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
        const newMsg: ChatMessage = {
          ...msg,
          id: assignedId,
          timestamp:
            msg.timestamp || new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }),
        };
        set((s) =>
          patchSession(s, sessionId, (snap) => ({
            ...snap,
            messages: [...snap.messages, newMsg],
          })),
        );
        return assignedId;
      },
      removeMessage: (id, sessionId) =>
        set((s) =>
          patchSession(s, sessionId, (snap) => ({ ...snap, messages: snap.messages.filter((m) => m.id !== id) })),
        ),
      removeLastFailedMessage: (sessionId) =>
        set((s) =>
          patchSession(s, sessionId, (snap) =>
            snap.messages[snap.messages.length - 1]?.failed ? { ...snap, messages: snap.messages.slice(0, -1) } : snap,
          ),
        ),
      appendToLastMessage: (chunk, sessionId) =>
        set((s) =>
          patchSession(s, sessionId, (snap) => {
            const last = snap.messages[snap.messages.length - 1];
            if (!last) return snap;
            return { ...snap, messages: [...snap.messages.slice(0, -1), { ...last, content: last.content + chunk }] };
          }),
        ),
      appendToMessage: (id, chunk, sessionId) =>
        set((s) =>
          patchSession(s, sessionId, (snap) => {
            const index = snap.messages.findIndex((m) => m.id === id);
            if (index < 0) return snap;
            return {
              ...snap,
              messages: snap.messages.map((m) => (m.id === id ? { ...m, content: (m.content || "") + chunk } : m)),
            };
          }),
        ),
      updateMessage: (id, patch, sessionId) =>
        set((s) =>
          patchSession(s, sessionId, (snap) =>
            snap.messages.some((m) => m.id === id)
              ? { ...snap, messages: snap.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)) }
              : snap,
          ),
        ),
      updateLastMessage: (patch, sessionId) =>
        set((s) =>
          patchSession(s, sessionId, (snap) => {
            const last = snap.messages[snap.messages.length - 1];
            if (!last) return snap;
            return { ...snap, messages: [...snap.messages.slice(0, -1), { ...last, ...patch }] };
          }),
        ),
      addTile: (tile, sessionId) => {
        const s = get();
        // Канвас показывает открытую сессию: плитка фоновой уходит только в её кеш
        if ((sessionId ?? s.sessionId) === s.sessionId && !useCanvasStore.getState().dashboardId) {
          useCanvasStore.getState().addTile(tile);
        }
        set((st) => patchSession(st, sessionId, (snap) => ({ ...snap, tiles: [...snap.tiles, tile] })));
      },
      setTiles: (tiles) => {
        if (!useCanvasStore.getState().dashboardId) {
          useCanvasStore.getState().setTiles(tiles);
        }
        set((s) => patchSession(s, undefined, (snap) => ({ ...snap, tiles })));
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
      followAgentTable: (sessionId, nextTable) =>
        set((s) => {
          const currentTable = s.sessionActiveTable[sessionId];
          if (currentTable === nextTable) return {};
          const feeds = { ...(s.sessionTableCache[sessionId] || {}) };
          if (currentTable && feeds[currentTable]) {
            feeds[nextTable] = feeds[currentTable];
            delete feeds[currentTable];
          }
          return {
            sessionActiveTable: { ...s.sessionActiveTable, [sessionId]: nextTable },
            sessionTableCache: { ...s.sessionTableCache, [sessionId]: feeds },
          };
        }),
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
          const nextUsedIdeas = { ...s.usedIdeas };
          delete nextUsedIdeas[sessionId];
          const isActiveDeleted = s.sessionId === sessionId;
          return {
            databases: nextDatabases,
            sessionDatabase: nextSessionDb,
            sessionCache: nextCache,
            sessionActiveTable: nextActiveTable,
            sessionTableCache: nextTableCache,
            pendingQuestions: nextPending,
            usedIdeas: nextUsedIdeas,
            ...(isActiveDeleted ? { sessionId: null, messages: [], tiles: [], selectedTile: null } : {}),
          };
        }),
      updateTableBrief: (table: string, brief: TableBrief) =>
        set((s) => ({
          databases: s.databases.map((d) => {
            if (!d.tables.includes(table)) return d;
            const prevProfiles = d.profiles || [];
            const exists = prevProfiles.some((p) => p.table === table);
            const nextProfiles = exists
              ? prevProfiles.map((p) => (p.table === table ? brief : p))
              : [...prevProfiles, brief];
            return { ...d, profiles: nextProfiles };
          }),
        })),
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
        usedIdeas: state.usedIdeas,
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
