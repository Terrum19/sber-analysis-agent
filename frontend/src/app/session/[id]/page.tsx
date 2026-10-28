"use client";

/**
 * Вкладка сессии:
 * - Центр: CHAT (стриминг, превью дашборда)
 * - Справа: панель дашборда (графики, инсайты), скрываемая как панели Claude Code
 */
import { useEffect, useRef, useState } from "react";
import Splitter from "@/components/ui/Splitter";
import WorkspaceShell from "@/components/layout/WorkspaceShell";
import ChatPanel from "@/components/chat/ChatPanel";
import Canvas from "@/components/canvas/Canvas";
import { useSessionStore } from "@/store/sessionStore";
import { PANEL_WIDTH, useUiStore } from "@/store/uiStore";
import { useT } from "@/i18n/useT";

export default function SessionPage({ params }: { params: { id: string } }) {
  const t = useT();
  const setSession = useSessionStore((s) => s.setSession);
  const canvasOpen = useUiStore((s) => s.canvasOpen);
  const setCanvasOpen = useUiStore((s) => s.setCanvasOpen);
  const [mobileTab, setMobileTab] = useState<"chat" | "canvas">("chat");
  const panelWidth = useUiStore((s) => s.panelWidth);
  const setPanelWidth = useUiStore((s) => s.setPanelWidth);
  const dragStartWidth = useRef(panelWidth);

  useEffect(() => {
    if (params.id) {
      setSession(params.id);
    }
  }, [params.id, setSession]);

  return (
    <WorkspaceShell tab={{ kind: "session", id: params.id }}>
      {/* Переключатель Чат / Дашборд для экранов < lg */}
      <div className="flex lg:hidden items-center justify-center gap-1 border-b border-graphite-800 p-1.5 text-[12px]">
        {(
          [
            ["chat", t("tabs.chat")],
            ["canvas", t("tabs.dashboard")],
          ] as const
        ).map(([tab, label]) => (
          <button
            key={tab}
            onClick={() => {
              setMobileTab(tab);
              if (tab === "canvas") setCanvasOpen(true);
            }}
            className={`rounded-md px-3 py-1 transition ${
              mobileTab === tab ? "bg-graphite-800 text-graphite-50" : "text-graphite-400 hover:text-graphite-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <main className="flex flex-1 overflow-hidden">
        {/* ЧАТ */}
        <section
          className={`min-w-0 flex-1 overflow-hidden ${mobileTab === "chat" ? "flex flex-col" : "hidden lg:flex lg:flex-col"}`}
        >
          <ChatPanel sessionId={params.id} />
        </section>

        {/* ПАНЕЛЬ ДАШБОРДА: ширина меняется перетаскиванием разделителя */}
        {canvasOpen && (
          <>
            <div className="hidden lg:flex">
              <Splitter
                label={t("layout.resizePanel")}
                onDragStart={() => (dragStartWidth.current = panelWidth)}
                onDrag={(dx) => setPanelWidth(dragStartWidth.current - dx)}
                onReset={() => setPanelWidth(PANEL_WIDTH.initial)}
              />
            </div>
            <section
              style={mobileTab === "canvas" ? undefined : { width: panelWidth, maxWidth: "calc(100% - 320px)" }}
              className={`overflow-hidden p-2 pl-0 ${
                mobileTab === "canvas" ? "flex-1 flex flex-col pl-2" : "hidden shrink-0 lg:flex lg:flex-col"
              }`}
            >
              <Canvas sessionId={params.id} />
            </section>
          </>
        )}
      </main>
    </WorkspaceShell>
  );
}
