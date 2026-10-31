"use client";

/**
 * Верхняя строка рабочей области:
 * - Слева: вкладки как в VS Code (приветствие, дашборды, сессии, дашборды, артефакты);
 *   перетаскиваются мышью, полоса прокручивается колесом и уходит под панель справа
 * - Справа: «Поделиться» и показ правой панели (в сессии)
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { tableTitle, useSessionStore } from "@/store/sessionStore";
import { tabHref, tabKey, useUiStore, type WorkspaceTab } from "@/store/uiStore";
import { useChatRunStore } from "@/store/chatRunStore";
import { useT } from "@/i18n/useT";
import Icon, { type IconName } from "@/components/ui/Icon";
import RunDot from "./RunDot";
import ShareSessionButton from "./ShareSessionButton";

const TAB_ICON: Record<WorkspaceTab["kind"], IconName> = {
  welcome: "home",
  dashboards: "layout",
  session: "chat",
  dashboard: "layout",
  artifact: "chart",
};

function useTabTitle(tab: WorkspaceTab): string {
  const t = useT();
  const sessionTitle = useSessionStore((s) =>
    tab.kind === "session" ? s.databases.flatMap((d) => d.sessions).find((x) => x.id === tab.id)?.title : undefined,
  );
  const activeTableTitle = useActiveTableTitle(tab);
  switch (tab.kind) {
    case "welcome":
      return t("tabs.welcome");
    case "dashboards":
      return t("tabs.dashboards");
    case "session":
      return sessionTitle ?? activeTableTitle ?? t("sidebar.newSession");
    case "dashboard":
      return tab.title ?? t("tabs.dashboard");
    case "artifact":
      return tab.title ?? t("tabs.artifact");
  }
}

/** Название активной таблицы сессии для пользователя (display_name), без сессии — undefined. */
function useActiveTableTitle(tab: WorkspaceTab): string | undefined {
  return useSessionStore((s) => {
    if (tab.kind !== "session") return undefined;
    const table = s.sessionActiveTable[tab.id];
    if (!table) return undefined;
    return tableTitle(s.databases.find((d) => d.id === s.sessionDatabase[tab.id]), table);
  });
}

function Tab({
  tab,
  active,
  dragging,
  dropTarget,
  onDragStart,
  onDragEnter,
  onDragEnd,
}: {
  tab: WorkspaceTab;
  active: boolean;
  dragging: boolean;
  dropTarget: boolean;
  onDragStart: () => void;
  onDragEnter: () => void;
  onDragEnd: () => void;
}) {
  const t = useT();
  const router = useRouter();
  const closeTab = useUiStore((s) => s.closeTab);
  const title = useTabTitle(tab);
  const ref = useRef<HTMLAnchorElement>(null);
  const run = useChatRunStore((s) => (tab.kind === "session" ? s.runs[tab.id] : undefined));

  const databaseTitle = useSessionStore((s) => {
    if (tab.kind !== "session") return undefined;
    const dbId = s.sessionDatabase[tab.id];
    return s.databases.find((d) => d.id === dbId)?.title;
  });
  const activeTable = useActiveTableTitle(tab);

  // Активная вкладка всегда видна в полосе
  useEffect(() => {
    if (active) ref.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [active]);

  function handleClose(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    const next = closeTab(tab);
    if (active) router.push(next ? tabHref(next) : "/");
  }

  const tableNote = activeTable && activeTable !== title ? ` (${activeTable})` : "";
  const tooltipTitle = databaseTitle
    ? `${databaseTitle} / ${title}${tableNote}`
    : `${title}${tableNote}`;

  return (
    <Link
      ref={ref}
      href={tabHref(tab)}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", tabKey(tab));
        onDragStart();
      }}
      onDragEnter={onDragEnter}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => e.preventDefault()}
      onDragEnd={onDragEnd}
      onAuxClick={(e) => e.button === 1 && handleClose(e)}
      title={tooltipTitle}
      className={`group relative flex h-full max-w-[220px] shrink-0 items-center gap-2 border-r border-graphite-800 pl-3 pr-1.5 text-[12.5px] transition ${
        active
          ? "bg-graphite-900 text-graphite-50"
          : "bg-graphite-950 text-graphite-400 hover:bg-graphite-900/60 hover:text-graphite-200"
      } ${dragging ? "opacity-40" : ""}`}
    >
      {active && <span className="absolute inset-x-0 top-0 h-px bg-graphite-300" />}
      {dropTarget && <span className="absolute inset-y-1 left-0 w-0.5 rounded bg-graphite-300" />}
      <Icon name={TAB_ICON[tab.kind]} size={14} className="shrink-0 text-graphite-500" />
      <span className="truncate">{title}</span>
      <RunDot run={run} />
      <button
        type="button"
        onClick={handleClose}
        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded text-graphite-500 hover:bg-graphite-800 hover:text-graphite-100 ${
          active ? "opacity-100" : "opacity-0 group-hover:opacity-100"
        }`}
        title={t("tabs.close")}
      >
        <Icon name="x" size={12} />
      </button>
    </Link>
  );
}

export default function TopHeader({ active }: { active: WorkspaceTab }) {
  const t = useT();
  const tabs = useUiStore((s) => s.tabs);
  const moveTab = useUiStore((s) => s.moveTab);
  const canvasOpen = useUiStore((s) => s.canvasOpen);
  const setCanvasOpen = useUiStore((s) => s.setCanvasOpen);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [overKey, setOverKey] = useState<string | null>(null);
  const activeKey = tabKey(active);
  // Активная вкладка видна, даже если её закрыли и открыли по прямой ссылке
  const shown = tabs.some((x) => tabKey(x) === activeKey) ? tabs : [...tabs, active];

  return (
    <header className="relative flex h-10 w-full shrink-0 items-stretch border-b border-graphite-800 bg-graphite-950 text-graphite-300 select-none">
      {/* Вкладки: прокрутка колесом, перетаскивание для порядка */}
      <nav
        onWheel={(e) => {
          if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) e.currentTarget.scrollLeft += e.deltaY;
        }}
        className="flex min-w-0 flex-1 items-stretch overflow-x-auto pr-14 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {shown.map((tab) => {
          const key = tabKey(tab);
          return (
            <Tab
              key={key}
              tab={tab}
              active={key === activeKey}
              dragging={dragKey === key}
              dropTarget={dragKey !== null && overKey === key && dragKey !== key}
              onDragStart={() => setDragKey(key)}
              onDragEnter={() => setOverKey(key)}
              onDragEnd={() => {
                if (dragKey && overKey) moveTab(dragKey, overKey);
                setDragKey(null);
                setOverKey(null);
              }}
            />
          );
        })}
      </nav>

      {/* Панель справа: вкладки уходят под неё (сплошной фон и мягкий край) */}
      {active.kind === "session" && (
        <div className="absolute inset-y-0 right-0 flex items-center gap-1.5 bg-graphite-950 pl-2 pr-3 before:pointer-events-none before:absolute before:inset-y-0 before:right-full before:w-10 before:bg-gradient-to-l before:from-graphite-950 before:to-transparent">
          <ShareSessionButton sessionId={active.id} />
          <button
            onClick={() => setCanvasOpen(!canvasOpen)}
            className={`flex h-7 w-7 items-center justify-center rounded-lg transition ${
              canvasOpen ? "bg-graphite-800 text-graphite-100" : "text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100"
            }`}
            title={canvasOpen ? t("header.hidePanel") : t("header.showPanel")}
          >
            <Icon name="panelRight" />
          </button>
        </div>
      )}
    </header>
  );
}
