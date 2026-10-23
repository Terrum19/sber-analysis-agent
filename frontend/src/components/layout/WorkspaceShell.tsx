"use client";

/**
 * Оболочка рабочей области: сайдбар с базами данных (ширина меняется перетаскиванием),
 * полоса вкладок и содержимое активной вкладки. Открытие страницы добавляет её вкладку.
 */
import { useEffect, useRef } from "react";
import LeftSidebar from "@/components/layout/LeftSidebar";
import TopHeader from "@/components/layout/TopHeader";
import Splitter from "@/components/ui/Splitter";
import TableSelectModal from "@/components/modals/TableSelectModal";
import { useStartSession } from "@/hooks/useStartSession";
import { SIDEBAR_WIDTH, tabKey, useUiStore, type WorkspaceTab } from "@/store/uiStore";
import { useT } from "@/i18n/useT";

export default function WorkspaceShell({ tab, children }: { tab: WorkspaceTab; children: React.ReactNode }) {
  const t = useT();
  const openTab = useUiStore((s) => s.openTab);
  const sidebarWidth = useUiStore((s) => s.sidebarWidth);
  const setSidebarWidth = useUiStore((s) => s.setSidebarWidth);
  const tableModalTarget = useUiStore((s) => s.tableModalTarget);
  const setTableModalTarget = useUiStore((s) => s.setTableModalTarget);
  const startSession = useStartSession();
  const dragStartWidth = useRef(sidebarWidth);
  const key = tabKey(tab);
  const title = "title" in tab ? tab.title : undefined;

  useEffect(() => {
    openTab(tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, title, openTab]);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-graphite-950 font-sans">
      <div className="hidden shrink-0 border-r border-graphite-800 md:block" style={{ width: sidebarWidth }}>
        <LeftSidebar />
      </div>
      <div className="hidden md:flex -ml-0.5">
        <Splitter
          label={t("layout.resizeSidebar")}
          onDragStart={() => (dragStartWidth.current = sidebarWidth)}
          onDrag={(dx) => setSidebarWidth(dragStartWidth.current + dx)}
          onReset={() => setSidebarWidth(SIDEBAR_WIDTH.initial)}
        />
      </div>
      <div className="-ml-0.5 flex min-w-0 flex-1 flex-col overflow-hidden">
        <TopHeader active={tab} />
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-graphite-900">{children}</div>
      </div>
      {tableModalTarget && (
        <TableSelectModal
          isOpen={Boolean(tableModalTarget)}
          databaseTitle={tableModalTarget.title}
          tables={tableModalTarget.tables}
          profiles={tableModalTarget.profiles}
          onClose={() => setTableModalTarget(null)}
          onSelect={async (table) => {
            await startSession(tableModalTarget, table);
          }}
        />
      )}
    </div>
  );
}
