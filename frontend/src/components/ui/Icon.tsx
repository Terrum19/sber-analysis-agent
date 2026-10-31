/**
 * Линейные иконки интерфейса (16px, stroke 1.5, цвет — currentColor).
 */
import type { SVGProps } from "react";

const PATHS = {
  plus: "M12 5v14M5 12h14",
  chevronRight: "m9 6 6 6-6 6",
  chevronDown: "m6 9 6 6 6-6",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  folderOpen: "M3 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v1M3 7v10a2 2 0 0 0 2 2h12l3.5-7.5A1 1 0 0 0 19.6 10H7.4a1 1 0 0 0-.9.6L3 17",
  chat: "M4 5h16v11H8l-4 4z",
  upload: "M12 15V4m0 0-4 4m4-4 4 4M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3",
  database: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  layout: "M4 4h7v9H4zM13 4h7v5h-7zM13 11h7v9h-7zM4 15h7v5H4z",
  x: "M6 6l12 12M18 6 6 18",
  refresh: "M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5",
  download: "M12 4v11m0 0-4-4m4 4 4-4M4 19h16",
  pencil: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  panelRight: "M4 5h16v14H4zM15 5v14",
  book: "M5 4h10a4 4 0 0 1 4 4v12H9a4 4 0 0 1-4-4zM5 16a4 4 0 0 1 4-4h10",
  sparkle: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6",
  arrowUp: "M12 19V5m0 0-6 6m6-6 6 6",
  stop: "M7 7h10v10H7z",
  save: "M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6",
  check: "m5 12 5 5L20 7",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.5 2.6 3.7 5.6 3.7 9s-1.2 6.4-3.7 9c-2.5-2.6-3.7-5.6-3.7-9S9.5 5.6 12 3z",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  home: "M4 11 12 4l8 7M6 9.5V20h12V9.5",
  chart: "M4 20h16M7 16v-5M12 16V6M17 16v-8",
  chartBar: "M4 20h16M7 16v-5M12 16V6M17 16v-8",
  chartLine: "M4 17l6-6 4 4 6-8",
  chartArea: "M4 19h16v-6l-6-5-4 4-6-5v12z",
  chartScatter: "M7.5 16a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zm5-5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zm5 3a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z",
  chartHistogram: "M4 20h16M6 16v-4h3v4M9 16V7h3v9M12 16v-6h3v6M15 16v-3h3v3",
  chartBox: "M12 3v4M7 7h10v10H7zM12 17v4M6 3h12M6 21h12M7 12h10",
  chartHeatmap: "M4 4h4v4H4zM10 4h4v4h-4zM16 4h4v4h-4zM4 10h4v4H4zM10 10h4v4h-4zM16 10h4v4h-4zM4 16h4v4H4zM10 16h4v4h-4zM16 16h4v4h-4z",
  chartTable: "M3 5h18v14H3zM3 10h18M9 5v14M15 5v14",
  chartKpi: "M7 4v16M17 4v16M4 9h16M4 15h16",
  maximize: "M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7",
  minus: "M5 12h14",
  moreHorizontal: "M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zm7 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM5 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
  trash: "M3 6h18m-2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m-6 5v6m4-6v6",
  pin: "M12 17v5M9 2h6M10 2v7l-2 3v2h8v-2l-2-3V2",
  alertTriangle: "m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3ZM12 9v4m0 4h.01",
  copy: "M8 8h12v12H8zM4 16V4h12",
  reply: "M9 7 4 12l5 5M4 12h9a7 7 0 0 1 7 7",
  share: "M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM8.6 13.5l6.8 4M15.4 6.5l-6.8 4",
} as const;

export type IconName = keyof typeof PATHS;

export default function Icon({
  name,
  size = 16,
  className,
  title,
  ...rest
}: { name: IconName; size?: number; title?: string } & Omit<SVGProps<SVGSVGElement>, "name">) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : "true"}
      className={className}
      {...rest}
    >
      {title && <title>{title}</title>}
      <path d={PATHS[name]} />
    </svg>
  );
}
