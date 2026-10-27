"use client";

/** Масштаб плиток: − / 100% / +. Значение общее для панели и страниц дашбордов. */
import { useUiStore, ZOOM } from "@/store/uiStore";
import { useT } from "@/i18n/useT";
import Icon from "@/components/ui/Icon";

export default function ZoomControl() {
  const t = useT();
  const zoom = useUiStore((s) => s.zoom);
  const setZoom = useUiStore((s) => s.setZoom);
  const button =
    "flex h-7 w-6 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100 disabled:opacity-40 disabled:hover:bg-transparent";

  return (
    <div className="flex items-center">
      <button className={button} onClick={() => setZoom(zoom - ZOOM.step)} disabled={zoom <= ZOOM.min} title={t("zoom.out")}>
        <Icon name="minus" size={14} />
      </button>
      <button
        onClick={() => setZoom(1)}
        className="h-7 min-w-[38px] rounded-md px-1 text-[11px] tabular-nums text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100"
        title={t("zoom.reset")}
      >
        {Math.round(zoom * 100)}%
      </button>
      <button className={button} onClick={() => setZoom(zoom + ZOOM.step)} disabled={zoom >= ZOOM.max} title={t("zoom.in")}>
        <Icon name="plus" size={14} />
      </button>
    </div>
  );
}
