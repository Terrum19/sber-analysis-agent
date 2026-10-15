"use client";

/**
 * Zustand-стор сессии: сообщения, готовые графики, выбранный элемент
 * канваса, подключённый источник данных. Держит состояние UI на клиенте;
 * данные — на бэкенде.
 */
import { create } from "zustand";
import type { Tile } from "@/types/chat";
import type { Datasource } from "@/types/datasource";

interface Message {
  role: "user" | "agent";
  content: string;
}

interface SessionState {
  sessionId: string | null;
  messages: Message[];
  tiles: Tile[];
  selectedTile: number | null;
  datasource: Datasource | null;
  setSession: (id: string) => void;
  addMessage: (msg: Message) => void;
  appendToLastMessage: (chunk: string) => void;
  addTile: (tile: Tile) => void;
  selectTile: (index: number | null) => void;
  setDatasource: (datasource: Datasource | null) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  sessionId: null,
  messages: [],
  tiles: [],
  selectedTile: null,
  datasource: null,
  setSession: (id) => set({ sessionId: id }),
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  appendToLastMessage: (chunk) =>
    set((s) => {
      const last = s.messages[s.messages.length - 1];
      if (!last) return {};
      return { messages: [...s.messages.slice(0, -1), { ...last, content: last.content + chunk }] };
    }),
  addTile: (tile) => set((s) => ({ tiles: [...s.tiles, tile] })),
  selectTile: (index) => set({ selectedTile: index }),
  setDatasource: (datasource) => set({ datasource }),
}));
