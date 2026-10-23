"use client";

/**
 * Состояние интерфейса, общее для сайдбара, вкладок и экрана приветствия:
 * тема и язык, ширины панелей и масштаб артефактов (сохраняются), окно подключения БД,
 * запрос выбора CSV-файлов, видимость правой панели, открытые вкладки рабочей области.
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { DatabaseFolder } from "@/store/sessionStore";

export type Theme = "dark" | "light";
export type Locale = "ru" | "en";

/** Вкладка рабочей области (как вкладки редактора VS Code). */
export type WorkspaceTab =
  | { kind: "welcome" }
  | { kind: "dashboards" }
  | { kind: "session"; id: string }
  | { kind: "dashboard"; id: string; title?: string }
  | { kind: "artifact"; id: string; title?: string };

export function tabKey(tab: WorkspaceTab): string {
  return "id" in tab ? `${tab.kind}:${tab.id}` : tab.kind;
}

export function tabHref(tab: WorkspaceTab): string {
  switch (tab.kind) {
    case "welcome":
      return "/";
    case "dashboards":
      return "/dashboards";
    case "session":
      return `/session/${tab.id}`;
    case "dashboard":
      return `/dashboard/${tab.id}`;
    case "artifact":
      return `/artifact/${tab.id}`;
  }
}

export const SIDEBAR_WIDTH = { min: 200, max: 440, initial: 256 };
export const PANEL_WIDTH = { min: 320, max: 1100, initial: 520 };
// Ниже 75% ряды сетки становятся ниже текста KPI — плитки перестают помещаться
export const ZOOM = { min: 0.75, max: 2, step: 0.25 };

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

interface UiState {
  theme: Theme;
  locale: Locale;
  dbModalOpen: boolean;
  tableModalTarget: DatabaseFolder | null;
  csvPickRequest: number;
  canvasOpen: boolean;
  tabs: WorkspaceTab[];
  sidebarWidth: number;
  panelWidth: number;
  /** Масштаб плиток в правой панели и на странице дашборда. */
  zoom: number;
  setTheme: (theme: Theme) => void;
  setLocale: (locale: Locale) => void;
  setDbModalOpen: (open: boolean) => void;
  setTableModalTarget: (target: DatabaseFolder | null) => void;
  requestCsvPick: () => void;
  setCanvasOpen: (open: boolean) => void;
  setSidebarWidth: (px: number) => void;
  setPanelWidth: (px: number) => void;
  setZoom: (zoom: number) => void;
  openTab: (tab: WorkspaceTab) => void;
  /** Переносит вкладку fromKey на место вкладки toKey (перетаскивание). */
  moveTab: (fromKey: string, toKey: string) => void;
  /** Закрывает вкладку и возвращает ту, что станет активной вместо неё. */
  closeTab: (tab: WorkspaceTab) => WorkspaceTab | null;
  /** Закрывает вкладки удалённых сессий и артефактов и возвращает новую активную вкладку. */
  closeTabsForSessions: (sessionIds: string[], artifactIds?: string[]) => WorkspaceTab | null;
}

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      theme: "dark",
      locale: "ru",
      dbModalOpen: false,
      tableModalTarget: null,
      csvPickRequest: 0,
      canvasOpen: true,
      tabs: [{ kind: "welcome" }],
      sidebarWidth: SIDEBAR_WIDTH.initial,
      panelWidth: PANEL_WIDTH.initial,
      zoom: 1,
      setTheme: (theme) => {
        // Сразу, до перерисовки: цвета графиков читаются из CSS-переменных темы
        if (typeof document !== "undefined") document.documentElement.dataset.theme = theme;
        set({ theme });
      },
      setLocale: (locale) => set({ locale }),
      setDbModalOpen: (dbModalOpen) => set({ dbModalOpen }),
      setTableModalTarget: (tableModalTarget) => set({ tableModalTarget }),
      requestCsvPick: () => set((s) => ({ csvPickRequest: s.csvPickRequest + 1 })),
      setCanvasOpen: (canvasOpen) => set({ canvasOpen }),
      setSidebarWidth: (px) => set({ sidebarWidth: clamp(Math.round(px), SIDEBAR_WIDTH.min, SIDEBAR_WIDTH.max) }),
      setPanelWidth: (px) => set({ panelWidth: clamp(Math.round(px), PANEL_WIDTH.min, PANEL_WIDTH.max) }),
      setZoom: (zoom) => set({ zoom: clamp(Math.round(zoom * 100) / 100, ZOOM.min, ZOOM.max) }),
      openTab: (tab) =>
        set((s) => {
          const index = s.tabs.findIndex((t) => tabKey(t) === tabKey(tab));
          if (index < 0) return { tabs: [...s.tabs, tab] };
          // Уже открыта: обновляем название вкладки дашборда или артефакта, если оно пришло
          const current = s.tabs[index] as { title?: string };
          if ("title" in tab && tab.title && current.title !== tab.title) {
            return { tabs: s.tabs.map((t, i) => (i === index ? tab : t)) };
          }
          return {};
        }),
      moveTab: (fromKey, toKey) =>
        set((s) => {
          const from = s.tabs.findIndex((t) => tabKey(t) === fromKey);
          const to = s.tabs.findIndex((t) => tabKey(t) === toKey);
          if (from < 0 || to < 0 || from === to) return {};
          const tabs = [...s.tabs];
          const [moved] = tabs.splice(from, 1);
          tabs.splice(to, 0, moved);
          return { tabs };
        }),
      closeTab: (tab) => {
        const tabs = get().tabs;
        const index = tabs.findIndex((t) => tabKey(t) === tabKey(tab));
        if (index < 0) return null;
        const next = tabs.filter((_, i) => i !== index);
        set({ tabs: next });
        return next[Math.min(index, next.length - 1)] ?? null;
      },
      closeTabsForSessions: (sessionIds, artifactIds = []) => {
        const sSet = new Set(sessionIds);
        const aSet = new Set(artifactIds);
        let nextTab: WorkspaceTab | null = null;
        set((s) => {
          const next = s.tabs.filter((t) => {
            if (t.kind === "session" && sSet.has(t.id)) return false;
            if (t.kind === "artifact" && aSet.has(t.id)) return false;
            return true;
          });
          const tabs = next.length > 0 ? next : [{ kind: "welcome" as const }];
          nextTab = tabs[tabs.length - 1] ?? null;
          return { tabs };
        });
        return nextTab;
      },
    }),
    {
      name: "sber_ui",
      storage: createJSONStorage(() => (typeof window !== "undefined" ? localStorage : ({} as any))),
      partialize: (s) => ({
        theme: s.theme,
        locale: s.locale,
        tabs: s.tabs,
        canvasOpen: s.canvasOpen,
        sidebarWidth: s.sidebarWidth,
        panelWidth: s.panelWidth,
        zoom: s.zoom,
      }),
    },
  ),
);
