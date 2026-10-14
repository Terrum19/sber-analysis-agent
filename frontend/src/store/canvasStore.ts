"use client";

/**
 * Zustand-стор канваса: раскладка плиток (для drag&drop на дашбордах),
 * запросы к агенту на дообследование выбранного элемента.
 */
import { create } from "zustand";

interface Layout {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface CanvasState {
  layout: Layout[];
  elaborateRequest: string | null;
  setLayout: (layout: Layout[]) => void;
  requestElaboration: (prompt: string | null) => void;
}

export const useCanvasStore = create<CanvasState>((set) => ({
  layout: [],
  elaborateRequest: null,
  setLayout: (layout) => set({ layout }),
  requestElaboration: (prompt) => set({ elaborateRequest: prompt }),
}));
