/**
 * Даты дашборда: backend отдаёт unix-секунды (snapshot_at) и unix-секунды
 * для created_at/updated_at, mock-режим — миллисекунды.
 */
import type { DashboardConfig } from "@/types/dashboard";

/** Универсальный парсер числа/строки/секунд в Date. */
export function toDate(value?: number | string | null): Date | null {
  if (value === undefined || value === null || value === "") return null;
  const d = typeof value === "number" && value < 1e11 ? new Date(value * 1000) : new Date(value);
  return isNaN(d.getTime()) ? null : d;
}

/**
 * Дата снимка дашборда: максимум snapshot_at его плиток.
 * У DashboardOut нет dashboard-уровневого snapshot_at, поэтому fallback —
 * updated_at, затем created_at. Результат — миллисекунды (или null).
 */
export function dashboardSnapshotAt(config: DashboardConfig): number | null {
  const stamps = (config.tiles ?? [])
    .map((tile) => toDate(tile.snapshot_at))
    .filter((value): value is Date => value !== null)
    .map((value) => value.getTime());
  if (stamps.length > 0) {
    return Math.max(...stamps);
  }
  const fallback = toDate(config.snapshot_at ?? config.updated_at) ?? toDate(config.created_at);
  return fallback ? fallback.getTime() : null;
}
