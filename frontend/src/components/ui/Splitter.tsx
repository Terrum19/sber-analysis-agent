"use client";

/**
 * Разделитель для изменения ширины панели перетаскиванием (как в VS Code).
 * onDrag получает смещение по X от начала перетаскивания; двойной клик — сброс.
 */
import { useRef } from "react";

export default function Splitter({
  onDragStart,
  onDrag,
  onReset,
  label,
}: {
  onDragStart: () => void;
  onDrag: (dx: number) => void;
  onReset?: () => void;
  label: string;
}) {
  const startX = useRef(0);

  function handlePointerDown(e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    startX.current = e.clientX;
    onDragStart();
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }

  function handlePointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
    onDrag(e.clientX - startX.current);
  }

  function handlePointerUp(e: React.PointerEvent<HTMLDivElement>) {
    e.currentTarget.releasePointerCapture(e.pointerId);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      title={label}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onDoubleClick={onReset}
      className="group relative z-20 w-1 shrink-0 cursor-col-resize touch-none"
    >
      <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition group-hover:w-0.5 group-hover:bg-graphite-500 group-active:w-0.5 group-active:bg-graphite-400" />
    </div>
  );
}
