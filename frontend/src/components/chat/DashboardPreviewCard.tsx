"use client";

/**
 * Превью дашборда в ленте чата: компактная сетка плиток из SSE-события
 * `dashboard` (без GET /api/dashboards/{id}) и две кнопки — полная страница
 * и правая панель (канвас).
 */
import DashboardTile from "@/components/canvas/DashboardTile";
import Icon from "@/components/ui/Icon";
import { useT } from "@/i18n/useT";
import { isKpiTile, previewGrid } from "@/lib/tileLayout";
import type { DashboardEvent } from "@/types/chat";

interface Props {
  dashboard: DashboardEvent["data"];
  onOpenDashboard: () => void;
  onOpenCanvas: () => void;
}

export default function DashboardPreviewCard({ dashboard, onOpenDashboard, onOpenCanvas }: Props) {
  const t = useT();
  const tiles = dashboard.tiles ?? [];
  const items = previewGrid(tiles, isKpiTile);

  return (
    <div className="mt-2 w-full max-w-xl rounded-xl border border-graphite-800 bg-graphite-850 p-2.5">
      {/* Шапка: название, число плиток и действия */}
      <div className="mb-2 flex items-center gap-2">
        <Icon name="layout" className="shrink-0 text-graphite-400" />
        <div className="min-w-0 flex-1">
          <span className="block truncate text-[12.5px] font-medium text-graphite-100">
            {dashboard.title || t("chat.dashboardDefault")}
          </span>
          <span className="block text-[10px] text-graphite-500">
            {t("chat.dashboardTiles", { n: tiles.length })}
          </span>
        </div>
        <button
          onClick={onOpenCanvas}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100"
          title={t("chat.toCanvasTitle")}
          aria-label={t("chat.toCanvasTitle")}
        >
          <Icon name="panelRight" size={15} />
        </button>
        <button
          onClick={onOpenDashboard}
          className="flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[11px] text-graphite-300 hover:bg-graphite-800 hover:text-graphite-100"
          title={t("chat.openDashboard")}
        >
          <Icon name="maximize" size={14} />
          <span className="hidden sm:inline">{t("chat.openDashboard")}</span>
        </button>
      </div>

      {items.length === 0 ? (
        // Старый бэкенд: tiles в событии нет. GET не делаем — полная страница
        // загрузит дашборд сама после перехода.
        <p className="rounded-lg border border-dashed border-graphite-700 px-2.5 py-2 text-[11px] leading-snug text-graphite-400">
          {t("chat.dashboardPreparing")}
        </p>
      ) : (
        <div className="max-h-[460px] overflow-y-auto pr-1">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {items.map(({ key, tile, span }) => (
              <div
                key={key}
                className={
                  span === 2
                    ? "col-span-1 h-[170px] min-w-0 sm:col-span-2 sm:h-[190px]"
                    : "col-span-1 h-[104px] min-w-0"
                }
              >
                <DashboardTile tile={tile} preview />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
