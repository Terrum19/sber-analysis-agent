"use client";

/**
 * Плитка дашборда:
 * - График Vega-Lite или ECharts, оформленный под текущую тему и вписанный
 *   в размер ячейки (ничего не выходит за её границы)
 * - Таблица (usermeta.chart_type === "table") — HTML-таблицей, как в чате
 * - Или карточка KPI (большая цифра, изменение со стрелкой и смысловым цветом)
 * - Инсайт под графиком/KPI
 * - Режим редактирования (переименование, удаление)
 * - Клик открывает плитку на весь экран во вкладке (onOpen)
 * - Вариант mini — маленькое превью для реестра, large — вкладка артефакта
 */
import React, { useState } from "react";
import dynamic from "next/dynamic";
import { VegaLite, type VisualizationSpec } from "react-vega";
import type { EChartsOption } from "echarts";
import type { DashboardTile as DashboardTileType } from "@/types/dashboard";
import { deltaSign, echartsBase, themeVegaSpec, useChartColors, type ChartColors } from "@/lib/chartTheme";
import { useElementSize } from "@/hooks/useElementSize";
import { useT } from "@/i18n/useT";
import { isKpiTile } from "@/lib/tileLayout";
import { DataTable, chartKind } from "@/components/charts/ChartRenderer";
import Icon from "@/components/ui/Icon";

// isKpiTile живёт в lib/tileLayout (общая логика с раскладкой); реэкспорт —
// чтобы существующие импорты из DashboardTile продолжали работать.
export { isKpiTile };

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

interface Props {
  tile: DashboardTileType;
  isEditing?: boolean;
  onRename?: (newTitle: string) => void;
  onDelete?: () => void;
  /** Открыть плитку на весь экран (клик по плитке вне режима редактирования). */
  onOpen?: () => void;
  /** Меню действий плитки: anchorRect — координаты кнопки «⋯». */
  onMenu?: (anchorRect: DOMRect) => void;
  /** Маленькое превью без осей и инсайта. */
  mini?: boolean;
  /** Компактная плитка в ленте чата: график/KPI + инсайт, без действий. */
  preview?: boolean;
  /** Плотный режим: минимальные KPI 2×2 и графики 4×3. */
  dense?: boolean;
  /** Плитка во всю вкладку: крупнее цифра, инсайт целиком. */
  large?: boolean;
}

function kpiFromTile(tile: DashboardTileType, fallbackLabel: string): { value: string; label: string } {
  const snapshot = tile.snapshot_data;
  let value = "—";
  let label = fallbackLabel;
  if (snapshot && typeof snapshot === "object") {
    if (snapshot.value !== undefined && snapshot.value !== null) value = String(snapshot.value);
    else if (snapshot.val !== undefined && snapshot.val !== null) value = String(snapshot.val);
    if (snapshot.label) label = String(snapshot.label);
    else if (snapshot.desc) label = String(snapshot.desc);
  } else if (tile.spec?.data && typeof tile.spec.data === "object" && "values" in tile.spec.data) {
    const vals = (tile.spec.data as { values?: any[] }).values;
    if (Array.isArray(vals) && vals.length > 0) {
      const first = vals[0];
      for (const k in first) {
        if (typeof first[k] === "number" || typeof first[k] === "string") {
          value = String(first[k]);
          label = k;
          break;
        }
      }
    }
  }
  return { value, label };
}

export function isEChartsTile(tile: DashboardTileType): boolean {
  return Boolean(
    !isKpiTile(tile) &&
      tile.spec &&
      ("series" in tile.spec ||
        tile.spec.renderer === "echarts" ||
        tile.spec.echarts === true ||
        tile.spec.type === "echarts" ||
        (tile.spec.usermeta as any)?.chart_type === "echarts"),
  );
}

/** Убирает оси и легенду (для превью): настройки в каналах сильнее общего config. */
function withoutAxes(spec: Record<string, any>): Record<string, any> {
  const encoding = Object.fromEntries(
    Object.entries((spec.encoding ?? {}) as Record<string, any>).map(([channel, def]) => [
      channel,
      def && typeof def === "object"
        ? {
            ...def,
            ...(channel === "x" || channel === "y" ? { axis: null } : {}),
            ...(channel === "color" ? { legend: null } : {}),
          }
        : def,
    ]),
  );
  return {
    ...spec,
    encoding,
    config: { ...spec.config, axis: { ...spec.config?.axis, disable: true }, legend: { disable: true } },
  };
}

/** График, растянутый на весь доступный контейнер. */
function FittedChart({ tile, colors, bare }: { tile: DashboardTileType; colors: ChartColors; bare?: boolean }) {
  const { ref, width, height } = useElementSize<HTMLDivElement>();
  const ready = width > 20 && height > 20;

  let content: React.ReactNode = null;
  if (ready && tile.spec && chartKind(tile.spec) === "table") {
    // Vega рисует таблицу текстовой сеткой: на ширине плитки колонки слипаются.
    content = <DataTable spec={tile.spec} height={height} />;
  } else if (ready && isEChartsTile(tile)) {
    const raw = (tile.spec?.option && typeof tile.spec.option === "object" ? tile.spec.option : tile.spec) as EChartsOption;
    content = (
      <ReactECharts option={{ ...echartsBase(colors), ...raw }} notMerge lazyUpdate style={{ width, height }} />
    );
  } else if (ready && tile.spec && Object.keys(tile.spec).length > 0) {
    let spec: Record<string, any> = {
      ...themeVegaSpec(tile.spec as Record<string, any>, colors),
      autosize: { type: "fit", contains: "padding" },
      width,
      height,
    };
    if (bare) spec = withoutAxes(spec);
    content = (
      <VegaLite
        spec={spec as VisualizationSpec}
        actions={false}
        onError={(err) => console.warn("VegaLite specification error:", err)}
      />
    );
  }

  return (
    <div ref={ref} className="relative min-h-0 flex-1 overflow-hidden">
      <div className="absolute inset-0">{content}</div>
    </div>
  );
}

export default function DashboardTile({
  tile,
  isEditing = false,
  onRename,
  onDelete,
  onOpen,
  onMenu,
  mini = false,
  preview = false,
  dense = false,
  large = false,
}: Props) {
  const t = useT();
  const colors = useChartColors();
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleInput, setTitleInput] = useState(tile.title);

  const isKpi = isKpiTile(tile);
  const hasChart = !isKpi && tile.spec && Object.keys(tile.spec).length > 0;
  const kpi = isKpi ? kpiFromTile(tile, t("tile.kpiDefault")) : null;
  const sign = kpi ? deltaSign(kpi.label) : 0;
  const canOpen = Boolean(onOpen) && !isEditing;

  function handleSaveTitle() {
    setEditingTitle(false);
    if (titleInput.trim() && titleInput !== tile.title) {
      onRename?.(titleInput.trim());
    } else {
      setTitleInput(tile.title);
    }
  }

  function handleCancelTitle() {
    setTitleInput(tile.title);
    setEditingTitle(false);
  }

  if (preview) {
    return (
      <div className="flex h-full min-w-0 flex-col overflow-hidden rounded-lg border border-graphite-800 bg-graphite-900 p-2">
        <div className="mb-1 flex shrink-0 items-center gap-1.5">
          {isKpi && (
            <span className="shrink-0 rounded border border-kpi/30 bg-kpi/10 px-1 py-0.5 text-[9px] font-semibold text-kpi">
              KPI
            </span>
          )}
          {tile.stale && (
            <span
              className="shrink-0 rounded border border-amber-500/30 bg-amber-500/10 px-1 text-[8px] font-semibold uppercase tracking-wider text-amber-400"
              title={t("tile.staleTooltip")}
            >
              {t("tile.stale")}
            </span>
          )}
          <span
            className="truncate text-[10px] font-medium uppercase tracking-wide text-graphite-400"
            title={tile.title}
          >
            {tile.title}
          </span>
        </div>
        {isKpi && kpi ? (
          <div className="min-h-0 flex-1 overflow-hidden">
            <div className="truncate text-xl font-bold leading-6 text-graphite-50">{kpi.value}</div>
            <div className="mt-0.5 truncate text-[10px] text-graphite-400">{kpi.label}</div>
          </div>
        ) : hasChart ? (
          <div className="flex min-h-0 flex-1 overflow-hidden">
            <FittedChart tile={tile} colors={colors} />
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 items-center justify-center rounded bg-graphite-800/50 text-[10px] text-graphite-500">
            {t("tile.noData")}
          </div>
        )}
        {tile.insight && (
          <p
            className="mt-1 line-clamp-2 shrink-0 text-[10px] leading-snug text-graphite-300"
            title={tile.insight}
          >
            {tile.insight}
          </p>
        )}
      </div>
    );
  }

  if (mini) {
    return (
      <div className="flex h-full min-w-0 flex-col gap-1 overflow-hidden rounded-lg border border-graphite-800 bg-graphite-900 p-2">
        <span className="truncate text-[10px] font-medium uppercase tracking-wide text-graphite-400">{tile.title}</span>
        {isKpi && kpi ? (
          <div className="min-w-0">
            <div className="truncate text-lg font-bold leading-6 text-graphite-50">{kpi.value}</div>
            {sign !== 0 && (
              <div className={`truncate text-[10px] ${sign > 0 ? "text-good" : "text-bad"}`}>
                {sign > 0 ? "▲" : "▼"} {kpi.label}
              </div>
            )}
          </div>
        ) : hasChart ? (
          <div className="pointer-events-none flex min-h-0 flex-1">
            <FittedChart tile={tile} colors={colors} bare />
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div
      onClick={(e) => {
        if (!canOpen) return;
        // Кнопки и поля внутри плитки работают как обычно
        if ((e.target as HTMLElement).closest("button, input, a")) return;
        onOpen?.();
      }}
      className={`group relative flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-graphite-700/80 bg-graphite-900 shadow-sm transition-all duration-200 hover:border-graphite-600 hover:shadow-md ${
        dense ? "p-3" : "p-4"
      } ${canOpen ? "cursor-zoom-in" : ""}`}
    >
      {/* Верхняя строка плитки */}
      <div className={`flex shrink-0 items-start justify-between gap-2 ${dense ? "mb-1.5" : "mb-2"}`}>
        <div className="min-w-0 flex-1">
          {editingTitle ? (
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                value={titleInput}
                onChange={(e) => setTitleInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSaveTitle();
                  if (e.key === "Escape") handleCancelTitle();
                }}
                className="w-full rounded border border-graphite-400 bg-graphite-800 px-2 py-0.5 text-xs font-semibold text-graphite-100 outline-none"
                autoFocus
              />
              <button
                onClick={handleSaveTitle}
                className="rounded bg-graphite-100 px-2 py-0.5 text-[11px] text-graphite-950 hover:bg-graphite-50"
              >
                {t("tile.ok")}
              </button>
              <button
                onClick={handleCancelTitle}
                className="rounded border border-graphite-700 px-1.5 py-0.5 text-graphite-400 hover:text-graphite-50"
                title={t("tile.cancel")}
              >
                <Icon name="x" size={12} />
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-1.5">
              {isKpi && (
                <span className="shrink-0 rounded border border-kpi/30 bg-kpi/10 px-1.5 py-0.5 text-[10px] font-semibold text-kpi">
                  KPI
                </span>
              )}
              {tile.stale && (
                <span
                  className="shrink-0 rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-amber-400"
                  title={t("tile.staleTooltip")}
                >
                  {t("tile.stale")}
                </span>
              )}
              <h3
                className={`truncate font-semibold uppercase tracking-wider text-graphite-300 ${
                  large ? "text-sm" : dense ? "text-[11px]" : "text-xs"
                }`}
                title={tile.title}
              >
                {tile.title}
              </h3>
              {isEditing && (
                <button
                  onClick={() => setEditingTitle(true)}
                  className="shrink-0 text-graphite-400 hover:text-graphite-200"
                  title={t("tile.rename")}
                >
                  <Icon name="pencil" size={13} />
                </button>
              )}
            </div>
          )}
          {tile.result_table && !dense && (
            <span className="mt-0.5 block truncate font-mono text-[10px] text-graphite-500">src: {tile.result_table}</span>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {isEditing && onMenu && (
            <button
              onClick={(e) => onMenu(e.currentTarget.getBoundingClientRect())}
              className="flex h-6 w-6 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100 transition"
              title={t("canvas.tileMenu")}
            >
              <Icon name="moreHorizontal" size={14} />
            </button>
          )}
          {isEditing && onDelete ? (
            <button
              onClick={onDelete}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-bad/10 text-bad hover:bg-bad/20 transition"
              title={t("tile.delete")}
            >
              <Icon name="x" size={13} />
            </button>
          ) : (
            canOpen && (
              <button
                onClick={onOpen}
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-graphite-500 opacity-0 transition hover:bg-graphite-800 hover:text-graphite-100 group-hover:opacity-100 focus:opacity-100"
                title={t("artifact.open")}
              >
                <Icon name="maximize" size={13} />
              </button>
            )
          )}
        </div>
      </div>

      {/* Основной контент: KPI или график по размеру оставшегося места */}
      {isKpi && kpi ? (
        <div className="flex min-h-0 flex-1 flex-col justify-start overflow-hidden pt-1">
          <div
            className={`truncate font-extrabold tracking-tight text-graphite-50 ${
              large ? "text-6xl" : dense ? "text-2xl" : "text-3xl"
            }`}
          >
            {kpi.value}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-graphite-400">
            <span
              className={`inline-flex items-center gap-1 font-medium ${
                sign > 0 ? "text-good" : sign < 0 ? "text-bad" : "text-graphite-300"
              }`}
            >
              {sign !== 0 && <span aria-hidden="true">{sign > 0 ? "▲" : "▼"}</span>}
              {kpi.label}
            </span>
            {!dense && <span className="text-[11px] text-graphite-500">{t("tile.kpiSlice")}</span>}
          </div>
        </div>
      ) : hasChart ? (
        <FittedChart tile={tile} colors={colors} />
      ) : (
        <div className="flex min-h-0 flex-1 items-center justify-center rounded bg-graphite-800/50 text-xs text-graphite-500">
          {t("tile.noData")}
        </div>
      )}

      {/* Инсайт под графиком/KPI; в плотном KPI-режиме он не помещается */}
      {tile.insight && !(dense && isKpi) && (
        <div
          className={`flex shrink-0 items-start gap-2 rounded-lg border border-graphite-800 bg-graphite-950/60 ${
            dense ? "mt-1 p-1.5" : "mt-2 p-2.5"
          }`}
        >
          <Icon name="sparkle" size={14} className="mt-0.5 shrink-0 text-graphite-400" />
          <p
            className={`leading-relaxed text-graphite-300 ${
              large ? "text-[13px]" : dense ? "line-clamp-1 text-[10px]" : "line-clamp-2 text-[11px]"
            }`}
            title={tile.insight}
          >
            {tile.insight}
          </p>
        </div>
      )}
    </div>
  );
}
