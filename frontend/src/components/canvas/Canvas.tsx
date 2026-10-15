"use client";

/**
 * Канвас: элементы, на которые можно нажать и попросить нейронку
 * проэлаборировать/дообследовать момент (план-максимум).
 */
import { useSessionStore } from "@/store/sessionStore";
import { useCanvasStore } from "@/store/canvasStore";
import DashboardTile from "@/components/canvas/DashboardTile";

export default function Canvas({ sessionId }: { sessionId: string }) {
  const tiles = useSessionStore((s) => s.tiles);
  const selectTile = useSessionStore((s) => s.selectTile);
  const requestElaboration = useCanvasStore((s) => s.requestElaboration);

  return (
    <div className="h-full overflow-y-auto p-4">
      {tiles.length === 0 ? (
        <p className="text-slate-400">Здесь появятся графики и дашборды из диалога.</p>
      ) : (
        <div className="grid grid-cols-2 gap-4">
          {tiles.map((tile, i) => (
            <button key={i} onClick={() => { selectTile(i); requestElaboration(`Расскажи подробнее про график: ${tile.title}`); }}>
              <DashboardTile tile={tile} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
