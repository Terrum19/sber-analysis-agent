"use client";

/**
 * Zustand-стор канваса дашборда: плитки, активный дашборд, режим редактирования,
 * синхронизация с бэкендом (сохранение / пересчёт).
 */
import { create } from "zustand";
import type { DashboardTile, DashboardTileLayout } from "@/types/dashboard";

export type SaveState = "idle" | "saving" | "saved" | "error";

interface CanvasState {
  dashboardId: string | null;
  dashboardTitle: string;
  tiles: DashboardTile[];
  selectedTileIndex: number | null;
  isEditing: boolean;
  isSaving: boolean;
  isRecalculating: boolean;
  lastSnapshotTime: string | null;
  elaborateRequest: string | null;
  /** Счётчик пользовательских изменений; автосохранение реагирует только на него. */
  layoutRevision: number;
  saveState: SaveState;
  saveError: string | null;

  setDashboard: (id: string | null, title: string, tiles: DashboardTile[], updatedAt?: string | number) => void;
  setTiles: (tiles: DashboardTile[]) => void;
  addTile: (tile: DashboardTile) => void;
  removeTile: (index: number) => void;
  updateTile: (index: number, patch: Partial<DashboardTile>) => void;
  updateTileLayout: (index: number, layout: DashboardTileLayout) => void;
  /** Батч-версия для onLayoutChange: один set вместо N подряд. */
  updateTileLayouts: (layouts: Record<number, DashboardTileLayout>) => void;
  setDashboardTitle: (title: string) => void;
  setSelectedTileIndex: (index: number | null) => void;
  setIsEditing: (editing: boolean) => void;
  setIsSaving: (saving: boolean) => void;
  setIsRecalculating: (recalc: boolean) => void;
  setLastSnapshotTime: (time: string | null) => void;
  requestElaboration: (prompt: string | null) => void;
  setSaveState: (state: SaveState, error?: unknown) => void;
  clearCanvas: () => void;
}

function errorMessage(error: unknown): string {
  if (!error) return "";
  const err = error as { detail?: unknown; message?: unknown };
  if (typeof err.detail === "string") return err.detail;
  if (typeof err.message === "string") return err.message;
  return String(error);
}

export const useCanvasStore = create<CanvasState>((set) => ({
  dashboardId: null,
  dashboardTitle: "",
  tiles: [],
  selectedTileIndex: null,
  isEditing: false,
  isSaving: false,
  isRecalculating: false,
  lastSnapshotTime: null,
  elaborateRequest: null,
  layoutRevision: 0,
  saveState: "idle",
  saveError: null,

  setDashboard: (id, title, tiles, updatedAt) =>
    set({
      dashboardId: id,
      dashboardTitle: title || "",
      tiles: tiles || [],
      lastSnapshotTime: updatedAt ? new Date(typeof updatedAt === "number" && updatedAt < 1e11 ? updatedAt * 1000 : updatedAt).toLocaleTimeString("ru-RU") : new Date().toLocaleTimeString("ru-RU"),
      // Загрузка нового дашборда не должна запускать автосохранение
      layoutRevision: 0,
      saveState: "idle",
      saveError: null,
    }),

  // Загрузка/пересчёт, а не действие пользователя: revision не растёт
  setTiles: (tiles) => set({ tiles }),

  addTile: (tile) => set((s) => ({ tiles: [...s.tiles, tile], layoutRevision: s.layoutRevision + 1 })),

  removeTile: (index) =>
    set((s) => ({
      tiles: s.tiles.filter((_, i) => i !== index),
      selectedTileIndex: s.selectedTileIndex === index ? null : s.selectedTileIndex,
      layoutRevision: s.layoutRevision + 1,
    })),

  updateTile: (index, patch) =>
    set((s) => ({
      tiles: s.tiles.map((t, i) => (i === index ? { ...t, ...patch } : t)),
      layoutRevision: s.layoutRevision + 1,
    })),

  updateTileLayout: (index, layout) =>
    set((s) => ({
      tiles: s.tiles.map((t, i) => (i === index ? { ...t, layout } : t)),
      layoutRevision: s.layoutRevision + 1,
    })),

  updateTileLayouts: (layouts) =>
    set((s) => ({
      tiles: s.tiles.map((t, i) => (layouts[i] ? { ...t, layout: layouts[i] } : t)),
      layoutRevision: s.layoutRevision + 1,
    })),

  setDashboardTitle: (title) => set({ dashboardTitle: title }),

  setSelectedTileIndex: (index) => set({ selectedTileIndex: index }),

  setIsEditing: (isEditing) => set({ isEditing }),

  setIsSaving: (isSaving) => set({ isSaving }),

  setIsRecalculating: (isRecalculating) => set({ isRecalculating }),

  setLastSnapshotTime: (lastSnapshotTime) => set({ lastSnapshotTime }),

  requestElaboration: (prompt) => set({ elaborateRequest: prompt }),

  setSaveState: (state, error) =>
    set({ saveState: state, saveError: state === "error" ? errorMessage(error) || "Unknown error" : null }),

  clearCanvas: () =>
    set({
      dashboardId: null,
      dashboardTitle: "",
      tiles: [],
      selectedTileIndex: null,
      isEditing: false,
      layoutRevision: 0,
      saveState: "idle",
      saveError: null,
    }),
}));
