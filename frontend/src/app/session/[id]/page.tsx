"use client";

/**
 * Экран сессии: чат слева, канвас справа (план-максимум).
 * План-минимум: только чат со стримингом.
 */
import ChatPanel from "@/components/chat/ChatPanel";
import Canvas from "@/components/canvas/Canvas";

export default function SessionPage({ params }: { params: { id: string } }) {
  return (
    <main className="flex h-screen">
      <section className="w-1/2 border-r border-slate-200">
        <ChatPanel sessionId={params.id} />
      </section>
      <section className="w-1/2">
        <Canvas sessionId={params.id} />
      </section>
    </main>
  );
}
