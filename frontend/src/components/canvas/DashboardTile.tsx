"use client";

/**
 * Плитка дашборда:
 * - График Vega-Lite или ECharts, оформленный под текущую тему и вписанный
 *   в размер ячейки (ничего не выходит за её границы)
 * - Таблица (usermeta.chart_type === "table") — HTML-таблицей, как в чате
 * - Или карточка KPI (большая цифра, изменение со стрелкой и смысловым цветом)
 * - Инсайт под графиком/KPI
 * - Режим редактирования (переименование, удаление)
 * - «＋ в дашборды» и «Открыть чат» в шапке (F-15)
 * - Клик по точке графика — «дообследуй» (onElaborate, F-09);
 *   клик по плитке открывает её на весь экран (onOpen)
 * - Вариант mini — маленькое превью для реестра, large — вкладка артефакта
 */
import React, { useRef, useState } from "react";
import dynamic from "next/dynamic";
import { VegaLite, type VisualizationSpec } from "react-vega";
import type { EChartsOption } from "echarts";
import type { DashboardTile as DashboardTileType } from "@/types/dashboard";
import {
  VEGA_LOCALE,
  deltaSign,
  echartsBase,
  presentVegaSpec,
  themeVegaSpec,
  useChartColors,
  type ChartColors,
} from "@/lib/chartTheme";
import { useUiStore, type Locale } from "@/store/uiStore";
import {
  selectionFromECharts,
  selectionFromVega,
  type ChartSelection,
} from "@/lib/chartSelection";
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
  /** Плитка в сетке, которую можно тащить за шапку вне режима правки. */
  draggable?: boolean;
  onRename?: (newTitle: string) => void;
  onDelete?: () => void;
  /** Открыть плитку на весь экран (клик по плитке вне режима редактирования). */
  onOpen?: () => void;
  /** «Дообследовать»: клик по точке графика или по плитке (без точки). */
  onElaborate?: (selection?: ChartSelection) => void;
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
  /** «＋ в дашборды»: положить график в другие дашборды (F-15). */
  onAddToDashboards?: () => void;
  /** Перейти в чат, где построен график; chatDeleted — сессии больше нет. */
  onOpenChat?: () => void;
  chatDeleted?: boolean;
  /** Дополнительные действия в шапке плитки (например, экспорт графика, F-12). */
  actions?: React.ReactNode;
}

/** Число KPI: от миллиона — компактно («38,6 млн»), меньше — с разрядами; строки — как есть. */
function formatKpi(raw: unknown, locale: Locale): string {
  if (typeof raw !== "number") return String(raw);
  const tag = locale === "ru" ? "ru-RU" : "en-US";
  if (Math.abs(raw) >= 1e6) {
    return raw.toLocaleString(tag, { notation: "compact", maximumFractionDigits: 1 });
  }
  return raw.toLocaleString(tag, { maximumFractionDigits: 2 });
}

/** Поле значения KPI: encoding.text спеки, иначе первая числовая колонка строки. */
function kpiField(tile: DashboardTileType, row: Record<string, unknown>): string | undefined {
  const text = (tile.spec?.encoding as { text?: { field?: string } } | undefined)?.text;
  if (text?.field && text.field in row) return text.field;
  return Object.keys(row).find((key) => typeof row[key] === "number");
}

function kpiFromTile(
  tile: DashboardTileType,
  fallbackLabel: string,
  locale: Locale,
): { value: string; label: string; exact?: string } {
  const snapshot = tile.snapshot_data;
  let value = "—";
  let label = fallbackLabel;
  let exact: string | undefined;
  // Строки результата: снимок бэкенда (snapshot_records) или данные спеки —
  // в плитках из SSE-события снимка нет. KPI — первая строка.
  const specRows = (tile.spec?.data as { values?: unknown } | undefined)?.values;
  const rows = Array.isArray(snapshot) ? snapshot : !snapshot && Array.isArray(specRows) ? specRows : null;
  if (rows) {
    const row = rows[0];
    const field = row && typeof row === "object" ? kpiField(tile, row) : undefined;
    if (field !== undefined && row[field] !== null && row[field] !== undefined) {
      value = formatKpi(row[field], locale);
      const raw = row[field];
      if (typeof raw === "number") exact = raw.toLocaleString(locale === "ru" ? "ru-RU" : "en-US", { maximumFractionDigits: 2 });
      const text = (tile.spec?.encoding as { text?: { title?: string } } | undefined)?.text;
      // Сырое имя колонки (sum_revenue) подписью не показываем: заголовок плитки уже есть
      label = text?.title && text.title !== field ? text.title : "";
    }
  } else if (snapshot && typeof snapshot === "object") {
    if (snapshot.value !== undefined && snapshot.value !== null) value = String(snapshot.value);
    else if (snapshot.val !== undefined && snapshot.val !== null) value = String(snapshot.val);
    if (snapshot.label) label = String(snapshot.label);
    else if (snapshot.desc) label = String(snapshot.desc);
  }
  return { value, label, exact };
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
function FittedChart({
  tile,
  colors,
  bare,
  onSelect,
}: {
  tile: DashboardTileType;
  colors: ChartColors;
  bare?: boolean;
  onSelect?: (selection: ChartSelection) => void;
}) {
  const { ref, width, height } = useElementSize<HTMLDivElement>();
  const locale = useUiStore((s) => s.locale);
  const ready = width > 20 && height > 20;
  // Обработчик держим в ref: Vega-вью переиспользуется при изменении размера,
  // а колбэк из родителя меняется на каждом рендере.
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const listeningViews = useRef<WeakSet<object>>(new WeakSet());

  let content: React.ReactNode = null;
  if (ready && tile.spec && chartKind(tile.spec) === "table") {
    // Vega рисует таблицу текстовой сеткой: на ширине плитки колонки слипаются.
    content = <DataTable spec={tile.spec} height={height} />;
  } else if (ready && isEChartsTile(tile)) {
    const raw = (tile.spec?.option && typeof tile.spec.option === "object" ? tile.spec.option : tile.spec) as EChartsOption;
    content = (
      <ReactECharts
        option={{ ...echartsBase(colors), ...raw }}
        notMerge
        lazyUpdate
        style={{ width, height }}
        onEvents={{
          click: (params: unknown) => {
            if (!onSelectRef.current) return;
            const selection = selectionFromECharts(params, tile.spec);
            if (!selection) return;
            // Клик по точке не должен открывать плитку/артефакт.
            (params as { event?: { event?: { stopPropagation?: () => void } } })?.event?.event?.stopPropagation?.();
            onSelectRef.current(selection);
          },
        }}
      />
    );
  } else if (ready && tile.spec && Object.keys(tile.spec).length > 0) {
    // Название графика — в шапке плитки, внутри спеки его не повторяем
    const presented = presentVegaSpec(themeVegaSpec(tile.spec as Record<string, any>, colors), {
      width,
      height,
      locale,
      colors,
      labels: !bare,
    });
    let spec: Record<string, any> = {
      ...presented,
      autosize: { type: "fit", contains: "padding" },
      width,
      height: typeof presented.height === "number" ? Math.min(height, presented.height) : height,
    };
    if (bare) spec = withoutAxes(spec);
    content = (
      <VegaLite
        spec={spec as VisualizationSpec}
        actions={false}
        formatLocale={VEGA_LOCALE[locale].formatLocale as unknown as Record<string, unknown>}
        timeFormatLocale={VEGA_LOCALE[locale].timeFormatLocale as unknown as Record<string, unknown>}
        onError={(err) => console.warn("VegaLite specification error:", err)}
        onNewView={(view: any) => {
          if (listeningViews.current.has(view)) return;
          listeningViews.current.add(view);
          view.addEventListener("click", (event: any, item: any) => {
            if (!item?.datum || !onSelectRef.current) return;
            const selection = selectionFromVega(item.datum, tile.spec);
            if (!selection) return;
            event?.stopPropagation?.();
            onSelectRef.current(selection);
          });
        }}
      />
    );
  }

  return (
    <div ref={ref} className="relative min-h-0 flex-1 overflow-hidden">
      <div className="absolute inset-0 flex flex-col justify-center">{content}</div>
    </div>
  );
}

export default function DashboardTile({
  tile,
  isEditing = false,
  onRename,
  onDelete,
  onOpen,
  onElaborate,
  onMenu,
  onAddToDashboards,
  onOpenChat,
  chatDeleted = false,
  mini = false,
  preview = false,
  dense = false,
  large = false,
  draggable = false,
  actions,
}: Props) {
  const t = useT();
  const colors = useChartColors();
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleInput, setTitleInput] = useState(tile.title);

  React.useEffect(() => {
    setTitleInput(tile.title);
  }, [tile.title]);

  const isKpi = isKpiTile(tile);
  const hasChart = !isKpi && tile.spec && Object.keys(tile.spec).length > 0;
  const locale = useUiStore((s) => s.locale);
  const kpi = isKpi ? kpiFromTile(tile, t("tile.kpiDefault"), locale) : null;
  const sign = kpi ? deltaSign(kpi.label) : 0;
  const canOpen = Boolean(onOpen) && !isEditing;
  const canDrill = Boolean(onElaborate) && !isEditing;

  function handlePointSelect(selection: ChartSelection) {
    onElaborate?.(selection);
  }

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
        if (isEditing) return;
        // Кнопки и поля внутри плитки работают как обычно; шапка — ручка перетаскивания
        if ((e.target as HTMLElement).closest("button, input, a, .tile-drag-handle")) return;
        if (canOpen) {
          onOpen?.();
        }
      }}
      className={`group relative flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-graphite-700/80 bg-graphite-900 shadow-sm transition-all duration-200 hover:border-graphite-600 hover:shadow-md ${
        dense ? "p-3" : "p-4"
      } ${canOpen ? "cursor-zoom-in" : ""}`}
    >
      {/* Верхняя строка плитки; вне режима правки — ручка перетаскивания */}
      <div
        className={`flex shrink-0 items-start justify-between gap-2 ${dense ? "mb-1.5" : "mb-2"} ${
          draggable && !isEditing ? "tile-drag-handle cursor-grab active:cursor-grabbing" : ""
        }`}
      >
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
                } ${onRename ? "cursor-text" : ""}`}
                title={tile.result_table ? `${tile.title} · ${t("tile.source")}: ${tile.result_table}` : tile.title}
                onDoubleClick={() => {
                  if (onRename) setEditingTitle(true);
                }}
              >
                {tile.title}
              </h3>
              {(isEditing || Boolean(onRename)) && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditingTitle(true);
                  }}
                  className={`shrink-0 text-graphite-400 hover:text-graphite-200 transition ${
                    isEditing ? "" : "opacity-0 group-hover:opacity-100"
                  }`}
                  title={t("tile.rename")}
                >
                  <Icon name="pencil" size={13} />
                </button>
              )}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {actions && !dense && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
          {canDrill && (
            <button
              onClick={() => onElaborate?.()}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100 transition"
              title={t("drill.menu")}
            >
              <Icon name="sparkle" size={13} />
            </button>
          )}
          {onOpenChat && (
            <button
              onClick={onOpenChat}
              disabled={chatDeleted}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-graphite-400 transition hover:bg-graphite-800 hover:text-graphite-100 disabled:cursor-not-allowed disabled:opacity-40"
              title={chatDeleted ? t("tile.chatDeleted") : t("tile.openChat")}
            >
              <Icon name="chat" size={13} />
            </button>
          )}
          {onAddToDashboards && (
            <button
              onClick={onAddToDashboards}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-graphite-400 transition hover:bg-graphite-800 hover:text-graphite-100"
              title={t("addTo.button")}
            >
              <Icon name="plus" size={13} />
            </button>
          )}
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
        // Число — главное в плитке: по центру, размер от места в плитке (container query)
        <div
          className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-hidden text-center"
          style={{ containerType: "size" }}
        >
          <div
            className="max-w-full truncate font-semibold leading-none tracking-tight text-graphite-50"
            style={{ fontSize: large ? "clamp(48px, min(45cqh, 14cqw), 112px)" : "clamp(22px, min(55cqh, 16cqw), 64px)" }}
            title={kpi.exact}
          >
            {kpi.value}
          </div>
          {kpi.label && (
            <div
              className={`mt-2 inline-flex max-w-full items-center gap-1 truncate text-xs font-medium ${
                sign > 0 ? "text-good" : sign < 0 ? "text-bad" : "text-graphite-400"
              }`}
            >
              {sign !== 0 && <span aria-hidden="true">{sign > 0 ? "▲" : "▼"}</span>}
              {kpi.label}
            </div>
          )}
        </div>
      ) : hasChart ? (
        <FittedChart
          tile={tile}
          colors={colors}
          onSelect={canDrill ? handlePointSelect : undefined}
        />
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
