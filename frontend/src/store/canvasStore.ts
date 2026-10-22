"use client";

/**
 * Zustand-стор канваса дашборда: плитки, активный дашборд, режим редактирования,
 * синхронизация с бэкендом (сохранение / пересчёт).
 */
import { create } from "zustand";
import type { DashboardTile, DashboardTileLayout } from "@/types/dashboard";

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

  setDashboard: (id: string | null, title: string, tiles: DashboardTile[], updatedAt?: string | number) => void;
  setTiles: (tiles: DashboardTile[]) => void;
  addTile: (tile: DashboardTile) => void;
  removeTile: (index: number) => void;
  updateTile: (index: number, patch: Partial<DashboardTile>) => void;
  updateTileLayout: (index: number, layout: DashboardTileLayout) => void;
  setDashboardTitle: (title: string) => void;
  setSelectedTileIndex: (index: number | null) => void;
  setIsEditing: (editing: boolean) => void;
  setIsSaving: (saving: boolean) => void;
  setIsRecalculating: (recalc: boolean) => void;
  setLastSnapshotTime: (time: string | null) => void;
  requestElaboration: (prompt: string | null) => void;
  clearCanvas: () => void;
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

  setDashboard: (id, title, tiles, updatedAt) =>
    set({
      dashboardId: id,
      dashboardTitle: title || "",
      tiles: tiles || [],
      lastSnapshotTime: updatedAt ? new Date(typeof updatedAt === "number" && updatedAt < 1e11 ? updatedAt * 1000 : updatedAt).toLocaleTimeString("ru-RU") : new Date().toLocaleTimeString("ru-RU"),
    }),

  setTiles: (tiles) => set({ tiles }),

  addTile: (tile) => set((s) => ({ tiles: [...s.tiles, tile] })),

  removeTile: (index) =>
    set((s) => ({
      tiles: s.tiles.filter((_, i) => i !== index),
      selectedTileIndex: s.selectedTileIndex === index ? null : s.selectedTileIndex,
    })),

  updateTile: (index, patch) =>
    set((s) => ({
      tiles: s.tiles.map((t, i) => (i === index ? { ...t, ...patch } : t)),
    })),

  updateTileLayout: (index, layout) =>
    set((s) => ({
      tiles: s.tiles.map((t, i) => (i === index ? { ...t, layout } : t)),
    })),

  setDashboardTitle: (title) => set({ dashboardTitle: title }),

  setSelectedTileIndex: (index) => set({ selectedTileIndex: index }),

  setIsEditing: (isEditing) => set({ isEditing }),

  setIsSaving: (isSaving) => set({ isSaving }),

  setIsRecalculating: (isRecalculating) => set({ isRecalculating }),

  setLastSnapshotTime: (lastSnapshotTime) => set({ lastSnapshotTime }),

  requestElaboration: (prompt) => set({ elaborateRequest: prompt }),

  clearCanvas: () =>
    set({
      dashboardId: null,
      dashboardTitle: "",
      tiles: [],
      selectedTileIndex: null,
      isEditing: false,
    }),
}));
