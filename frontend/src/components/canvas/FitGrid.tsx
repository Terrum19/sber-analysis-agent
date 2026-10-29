"use client";

/**
 * Сетка плиток react-grid-layout, ширина которой следует за контейнером
 * (ResizeObserver, а не только resize окна — важно для перетаскиваемых панелей).
 * zoom > 1 делает сетку шире контейнера с горизонтальной прокруткой.
 *
 * responsive: ResponsiveGridLayout с одним breakpoint lg: 0 и cols: 12 —
 * всегда 12 колонок, но ширина приходит из useElementSize, а не из окна.
 */
import { useEffect, useState, type ComponentType } from "react";
import RGL, {
  Responsive as ResponsiveRGL,
  type Layout,
  type ReactGridLayoutProps,
  type ResponsiveProps,
} from "react-grid-layout";
import { useElementSize } from "@/hooks/useElementSize";

// react-grid-layout — CommonJS пакет. Поддерживаем как default-экспорт, так и прямую функцию
const GridLayout = ((RGL as any).default || RGL) as ComponentType<ReactGridLayoutProps>;
const ResponsiveGridLayout = ((RGL as any).Responsive || ResponsiveRGL) as ComponentType<ResponsiveProps>;

type FitGridCommon = {
  zoom: number;
  layout: Layout[];
  responsive?: boolean;
  children: React.ReactNode;
  onLayoutChange?: (layout: Layout[]) => void;
};

type FitGridProps =
  | (Omit<ReactGridLayoutProps, "width" | "layout" | "onLayoutChange"> & FitGridCommon)
  | (Omit<ResponsiveProps, "width" | "layouts" | "onLayoutChange"> & FitGridCommon);

export default function FitGrid(props: FitGridProps) {
  const {
    zoom,
    layout,
    responsive = false,
    children,
    onLayoutChange,
    ...rest
  } = props as FitGridCommon & Record<string, any>;
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  const { ref, width } = useElementSize<HTMLDivElement>();
  const gridWidth = Math.max(240, Math.floor(width * zoom));

  return (
    <div ref={ref} className={zoom > 1 ? "overflow-x-auto" : "overflow-hidden"}>
      {mounted &&
        width > 0 &&
        (responsive ? (
          <ResponsiveGridLayout
            {...rest}
            width={gridWidth}
            breakpoints={{ lg: 0 }}
            cols={{ lg: 12 }}
            layouts={{ lg: layout }}
            onLayoutChange={(current: Layout[]) => onLayoutChange?.(current)}
          >
            {children}
          </ResponsiveGridLayout>
        ) : (
          <GridLayout {...rest} layout={layout} width={gridWidth} onLayoutChange={onLayoutChange}>
            {children}
          </GridLayout>
        ))}
    </div>
  );
}
