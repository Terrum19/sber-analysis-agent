"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { DashboardTile as DashboardTileType } from "@/types/dashboard";
import { withChartType, withRebuiltChartType, type ChartType } from "@/lib/chartTypes";
import { recalculateDashboard, updateDashboard } from "@/api/client";
import { useT } from "@/i18n/useT";

export interface UseChartTypeChangeOptions {
  dashboardId: string | null;
  tiles: DashboardTileType[];
  onTilesChange: (tiles: DashboardTileType[]) => void;
  onDashboardUpdated?: (updated: any) => void;
  setSaveState?: (state: "idle" | "saving" | "saved" | "error", err?: unknown) => void;
  onNotify?: (message: string) => void;
  debounceMs?: number;
}

export function useChartTypeChange({
  dashboardId,
  tiles,
  onTilesChange,
  onDashboardUpdated,
  setSaveState,
  onNotify,
  debounceMs = 500,
}: UseChartTypeChangeOptions) {
  const t = useT();
  const [pickerState, setPickerState] = useState<{
    tileIndex: number;
    anchorRect: DOMRect;
  } | null>(null);
  const [previewType, setPreviewType] = useState<ChartType | null>(null);

  const tilesRef = useRef(tiles);
  tilesRef.current = tiles;

  const dashboardIdRef = useRef(dashboardId);
  dashboardIdRef.current = dashboardId;

  const mountedRef = useRef(true);
  const saveSeqRef = useRef(0);
  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const initialTilesRef = useRef<DashboardTileType[] | null>(null);
  const pendingChangeRef = useRef<{
    tileIndex: number;
    type: ChartType;
    tiles: DashboardTileType[];
  } | null>(null);

  const lastClosedTimeRef = useRef(0);
  const lastClosedTileRef = useRef<number | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, []);

  const executeSave = useCallback(async () => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
      debounceTimerRef.current = null;
    }

    const pending = pendingChangeRef.current;
    if (!pending) return;
    pendingChangeRef.current = null;

    const seq = ++saveSeqRef.current;
    // Спеку пересобирает бэкенд по данным плитки: одна замена mark рисует таблицу
    // полосами, а heatmap / box / histogram без пересборки не меняются вовсе.
    const rebuilt = await withRebuiltChartType(pending.tiles[pending.tileIndex], pending.type);
    if (!mountedRef.current || seq !== saveSeqRef.current) return;
    const tiles = pending.tiles.map((item, i) => (i === pending.tileIndex ? rebuilt : item));
    onTilesChange(tiles);

    const currentDashboardId = dashboardIdRef.current;
    if (!currentDashboardId) {
      initialTilesRef.current = null;
      return;
    }

    setSaveState?.("saving");

    try {
      await updateDashboard(currentDashboardId, { tiles });
      if (!mountedRef.current || seq !== saveSeqRef.current) return;

      if (tiles[pending.tileIndex]?.result_table) {
        const updated = await recalculateDashboard(currentDashboardId);
        if (!mountedRef.current || seq !== saveSeqRef.current) return;
        onDashboardUpdated?.(updated);
        onTilesChange(updated.tiles);
        setSaveState?.("saved");
        initialTilesRef.current = null;
      } else {
        setSaveState?.("saved");
        onNotify?.(t("canvas.chartTypeLocalOnly"));
        initialTilesRef.current = null;
      }
    } catch (err: any) {
      if (!mountedRef.current || seq !== saveSeqRef.current) return;

      // При ошибке откатываемся к последнему сохранённому состоянию плиток
      if (initialTilesRef.current) {
        onTilesChange(initialTilesRef.current);
        initialTilesRef.current = null;
      }
      setSaveState?.("error", err);
      const msg = err?.detail || err?.message || String(err);
      onNotify?.(t("canvas.saveError", { msg }));
    }
  }, [onDashboardUpdated, onNotify, onTilesChange, setSaveState, t]);

  const closePicker = useCallback(() => {
    lastClosedTimeRef.current = Date.now();
    lastClosedTileRef.current = pickerState?.tileIndex ?? null;
    setPickerState(null);
    setPreviewType(null);

    // При закрытии панели немедленно сбрасываем дебаунс и сохраняем
    if (pendingChangeRef.current) {
      void executeSave();
    }
  }, [executeSave, pickerState]);

  const openPicker = useCallback(
    (tileIndex: number, anchorRect: DOMRect) => {
      // Повторный клик по кнопке той же плитки закрывает панель
      if (pickerState && pickerState.tileIndex === tileIndex) {
        closePicker();
        return;
      }

      // Защита от дребезга клика после mousedown на кнопке триггера
      const now = Date.now();
      if (
        now - lastClosedTimeRef.current < 200 &&
        lastClosedTileRef.current === tileIndex
      ) {
        return;
      }

      // Если была не сохранённая смена на другой плитке — сохраняем перед открытием новой
      if (pendingChangeRef.current) {
        void executeSave();
      }

      setPickerState({ tileIndex, anchorRect });
      setPreviewType(null);
    },
    [closePicker, executeSave, pickerState]
  );

  const setPreview = useCallback((type: ChartType | null) => {
    setPreviewType(type);
  }, []);

  const changeChartType = useCallback(
    (tileIndex: number, newType: ChartType) => {
      setPreviewType(null);
      const currentTiles = tilesRef.current;
      const tile = currentTiles[tileIndex];
      if (!tile) return;

      // Если это первый клик в серии быстрых переборов — фиксируем исходное состояние для отката
      if (initialTilesRef.current === null) {
        initialTilesRef.current = currentTiles;
      }

      // Применяем локально без задержек
      const nextTiles = currentTiles.map((item, i) =>
        i === tileIndex ? withChartType(item, newType) : item
      );
      onTilesChange(nextTiles);

      // Сбрасываем предыдущий таймер дебаунса
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }

      // Новый выбор делает предыдущий in-flight запрос устаревшим
      saveSeqRef.current++;

      pendingChangeRef.current = {
        tileIndex,
        type: newType,
        tiles: nextTiles,
      };

      debounceTimerRef.current = setTimeout(() => {
        void executeSave();
      }, debounceMs);
    },
    [debounceMs, executeSave, onTilesChange]
  );

  const getTile = useCallback(
    (tile: DashboardTileType, index: number): DashboardTileType => {
      if (pickerState && pickerState.tileIndex === index && previewType) {
        return withChartType(tile, previewType);
      }
      return tile;
    },
    [pickerState, previewType]
  );

  return {
    pickerState,
    openPicker,
    closePicker,
    previewType,
    setPreview,
    changeChartType,
    executeSave,
    getTile,
  };
}
