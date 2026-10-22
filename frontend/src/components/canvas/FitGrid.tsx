"use client";

/**
 * Сетка плиток react-grid-layout, ширина которой следует за контейнером
 * (ResizeObserver, а не только resize окна — важно для перетаскиваемых панелей).
 * zoom > 1 делает сетку шире контейнера с горизонтальной прокруткой.
 */
import dynamic from "next/dynamic";
import type { ReactGridLayoutProps } from "react-grid-layout";
import { useElementSize } from "@/hooks/useElementSize";

// Пакет CommonJS: класс сетки — экспорт по умолчанию
const GridLayout = dynamic(
  () =>
    import("react-grid-layout").then((mod) => {
      const lib = ((mod as any).default ?? mod) as typeof mod;
      return (lib.default ?? lib) as typeof mod.default;
    }),
  { ssr: false },
);

export default function FitGrid({
  zoom,
  children,
  ...props
}: Omit<ReactGridLayoutProps, "width"> & { zoom: number; children: React.ReactNode }) {
  const { ref, width } = useElementSize<HTMLDivElement>();
  const gridWidth = Math.max(240, Math.floor(width * zoom));

  return (
    <div ref={ref} className={zoom > 1 ? "overflow-x-auto" : "overflow-hidden"}>
      {width > 0 && (
        <GridLayout {...props} width={gridWidth}>
          {children}
        </GridLayout>
      )}
    </div>
  );
}
