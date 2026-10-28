"use client";

/**
 * Оболочка рабочей области: сайдбар с базами данных (ширина меняется перетаскиванием),
 * полоса вкладок и содержимое активной вкладки. Открытие страницы добавляет её вкладку.
 */
import { useEffect, useRef } from "react";
import LeftSidebar from "@/components/layout/LeftSidebar";
import TopHeader from "@/components/layout/TopHeader";
import Splitter from "@/components/ui/Splitter";
import ConnectDbModal from "@/components/modals/ConnectDbModal";
import TableSelectModal from "@/components/modals/TableSelectModal";
import Icon from "@/components/ui/Icon";
import { useStartSession } from "@/hooks/useStartSession";
import { SIDEBAR_WIDTH, tabKey, useUiStore, type WorkspaceTab } from "@/store/uiStore";
import { useT } from "@/i18n/useT";

export default function WorkspaceShell({ tab, children }: { tab: WorkspaceTab; children: React.ReactNode }) {
  const t = useT();
  const openTab = useUiStore((s) => s.openTab);
  const sidebarWidth = useUiStore((s) => s.sidebarWidth);
  const setSidebarWidth = useUiStore((s) => s.setSidebarWidth);
  const mobileSidebarOpen = useUiStore((s) => s.mobileSidebarOpen);
  const setMobileSidebarOpen = useUiStore((s) => s.setMobileSidebarOpen);
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
      {/* Десктопный сайдбар */}
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

      {/* Мобильная панель / Drawer */}
      {mobileSidebarOpen && (
        <div className="fixed inset-0 z-40 flex md:hidden">
          <div
            className="fixed inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
            onClick={() => setMobileSidebarOpen(false)}
          />
          <div className="relative flex w-72 max-w-[85vw] flex-col bg-graphite-950 shadow-2xl z-50 animate-in slide-in-from-left duration-200">
            <div className="absolute top-2 right-2 z-10">
              <button
                type="button"
                onClick={() => setMobileSidebarOpen(false)}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100 transition"
                title={t("common.close")}
              >
                <Icon name="x" size={18} />
              </button>
            </div>
            <LeftSidebar onAction={() => setMobileSidebarOpen(false)} />
          </div>
        </div>
      )}

      <div className="-ml-0.5 flex min-w-0 flex-1 flex-col overflow-hidden">
        <TopHeader active={tab} />
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-graphite-900">{children}</div>
      </div>

      <ConnectDbModal />

      {tableModalTarget && (
        <TableSelectModal
          isOpen={Boolean(tableModalTarget)}
          databaseTitle={tableModalTarget.title}
          tables={tableModalTarget.tables}
          briefs={tableModalTarget.profiles}
          onClose={() => setTableModalTarget(null)}
          onSelect={async (table) => {
            setTableModalTarget(null);
            await startSession(tableModalTarget, table);
          }}
        />
      )}
    </div>
  );
}
