"use client";

/**
 * Артефакты, открытые во вкладке на весь экран: плитка (график или KPI) с её данными.
 * Хранятся в браузере, чтобы вкладка пережила перезагрузку; держим последние 40.
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { DashboardTile } from "@/types/dashboard";

export interface ArtifactRecord {
  id: string;
  title: string;
  tile: DashboardTile;
  sessionId?: string;
  openedAt: number;
}

const LIMIT = 40;

interface ArtifactState {
  artifacts: Record<string, ArtifactRecord>;
  /** Сохраняет плитку как артефакт и возвращает его id. */
  saveArtifact: (tile: DashboardTile, sessionId?: string) => string;
  removeArtifactsBySessionIds: (sessionIds: string[]) => void;
}

function artifactId(tile: DashboardTile): string {
  if (tile.id) return tile.id;
  // Стабильный id по содержимому, чтобы повторное открытие не плодило вкладки
  const source = `${tile.title}|${tile.result_table ?? ""}`;
  let hash = 0;
  for (let i = 0; i < source.length; i++) hash = (hash * 31 + source.charCodeAt(i)) | 0;
  return `art-${(hash >>> 0).toString(36)}`;
}

export const useArtifactStore = create<ArtifactState>()(
  persist(
    (set) => ({
      artifacts: {},
      saveArtifact: (tile, sessionId) => {
        const id = artifactId(tile);
        set((s) => {
          const next = { ...s.artifacts, [id]: { id, title: tile.title, tile, sessionId, openedAt: Date.now() } };
          const ids = Object.values(next).sort((a, b) => b.openedAt - a.openedAt).map((a) => a.id);
          for (const old of ids.slice(LIMIT)) delete next[old];
          return { artifacts: next };
        });
        return id;
      },
      removeArtifactsBySessionIds: (sessionIds: string[]) => {
        const idSet = new Set(sessionIds);
        set((s) => {
          const next = { ...s.artifacts };
          let changed = false;
          for (const [id, art] of Object.entries(next)) {
            if (art.sessionId && idSet.has(art.sessionId)) {
              delete next[id];
              changed = true;
            }
          }
          return changed ? { artifacts: next } : {};
        });
      },
    }),
    {
      name: "sber_artifacts",
      storage: createJSONStorage(() => (typeof window !== "undefined" ? localStorage : ({} as any))),
    },
  ),
);
