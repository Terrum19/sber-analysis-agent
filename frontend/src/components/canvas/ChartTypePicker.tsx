"use client";

import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useT } from "@/i18n/useT";
import Icon from "@/components/ui/Icon";
import { CHART_TYPES, CHART_TYPE_ICONS, type ChartType } from "@/lib/chartTypes";

export interface ChartTypePickerProps {
  anchorRect: DOMRect | null;
  currentType: ChartType;
  onSelect: (type: ChartType) => void;
  onPreview: (type: ChartType | null) => void;
  onClose: () => void;
}

export default function ChartTypePicker({
  anchorRect,
  currentType,
  onSelect,
  onPreview,
  onClose,
}: ChartTypePickerProps) {
  const t = useT();
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });

  useLayoutEffect(() => {
    if (!anchorRect || !menuRef.current) return;
    const { offsetWidth, offsetHeight } = menuRef.current;
    const padding = 8;

    let left = anchorRect.left;
    if (left + offsetWidth > window.innerWidth - padding) {
      left = Math.max(padding, anchorRect.right - offsetWidth);
    }
    if (left < padding) left = padding;

    let top = anchorRect.bottom + 4;
    if (top + offsetHeight > window.innerHeight - padding) {
      top = Math.max(padding, anchorRect.top - offsetHeight - 4);
    }

    setPos({ top, left });
  }, [anchorRect]);

  useEffect(() => {
    function handlePointerDown(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onPreview(null);
        onClose();
      }
    }

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onPreview(null);
        onClose();
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose, onPreview]);

  if (!anchorRect) return null;

  return (
    <div
      ref={menuRef}
      role="dialog"
      aria-modal="false"
      aria-label={t("canvas.chartType")}
      style={{ top: `${pos.top}px`, left: `${pos.left}px` }}
      onClick={(e) => e.stopPropagation()}
      onPointerLeave={(e) => {
        if (e.pointerType === "touch") return;
        onPreview(null);
      }}
      className="fixed z-50 min-w-[210px] max-w-[260px] rounded-xl border border-graphite-700 bg-graphite-900 p-1.5 shadow-2xl text-graphite-100 select-none animate-in fade-in zoom-in-95 duration-100"
    >
      <div className="px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-graphite-400">
        {t("canvas.chartType")}
      </div>
      <div className="mt-1 space-y-0.5">
        {CHART_TYPES.map((type) => {
          const isSelected = type === currentType;
          return (
            <button
              key={type}
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onSelect(type);
              }}
              onPointerEnter={(e) => {
                if (e.pointerType === "touch") return;
                onPreview(type);
              }}
              className={`flex w-full items-center justify-between gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs transition ${
                isSelected
                  ? "bg-graphite-800 text-graphite-50 font-medium"
                  : "text-graphite-300 hover:bg-graphite-800/80 hover:text-graphite-100"
              }`}
            >
              <div className="flex items-center gap-2 min-w-0">
                <Icon
                  name={CHART_TYPE_ICONS[type] ?? "chart"}
                  size={14}
                  className={`shrink-0 ${isSelected ? "text-graphite-100" : "text-graphite-400"}`}
                />
                <span className="truncate">{t(`chartType.${type}` as any)}</span>
              </div>
              {isSelected && (
                <Icon name="check" size={14} className="shrink-0 text-good" />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
