"use client";

/**
 * Zustand-стор сессии: сообщения, готовые графики, выбранный элемент
 * канваса. Держит состояние UI на клиенте; данные — на бэкенде.
 */
import { create } from "zustand";
import type { Tile } from "@/types/chat";

interface Message {
  role: "user" | "agent";
  content: string;
}

interface SessionState {
  sessionId: string | null;
  messages: Message[];
  tiles: Tile[];
  selectedTile: number | null;
  setSession: (id: string) => void;
  addMessage: (msg: Message) => void;
  addTile: (tile: Tile) => void;
  selectTile: (index: number | null) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  sessionId: null,
  messages: [],
  tiles: [],
  selectedTile: null,
  setSession: (id) => set({ sessionId: id }),
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  addTile: (tile) => set((s) => ({ tiles: [...s.tiles, tile] })),
  selectTile: (index) => set({ selectedTile: index }),
}));
