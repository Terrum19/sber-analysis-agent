"use client";

/**
 * Вкладка артефакта (/artifact/[id]): одна плитка — график или KPI — на весь экран.
 * Плитка берётся из artifactStore, куда её кладёт клик по ней на панели или в дашборде.
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import WorkspaceShell from "@/components/layout/WorkspaceShell";
import DashboardTile from "@/components/canvas/DashboardTile";
import AnswerExport from "@/components/chat/AnswerExport";
import Icon from "@/components/ui/Icon";
import { useAskAgent } from "@/hooks/useAskAgent";
import { drillQuestion } from "@/lib/chartSelection";
import { useArtifactStore } from "@/store/artifactStore";
import { useT } from "@/i18n/useT";

export default function ArtifactPage({ params }: { params: { id: string } }) {
  const t = useT();
  const artifact = useArtifactStore((s) => s.artifacts[params.id]);
  const askAgent = useAskAgent();
  const sessionId = artifact?.sessionId;
  // Артефакты лежат в localStorage: до монтирования сервер их не видит
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <WorkspaceShell tab={{ kind: "artifact", id: params.id, title: artifact?.title }}>
      {!mounted ? null : artifact ? (
        <div className="flex min-h-0 flex-1 flex-col gap-3 p-4 md:p-6">
          {sessionId && (
            <div className="flex flex-wrap items-center gap-3">
              <Link
                href={`/session/${sessionId}`}
                className="flex w-fit items-center gap-1.5 text-[12px] text-graphite-400 hover:text-graphite-100"
              >
                <Icon name="chat" size={13} />
                {t("dashboard.toSession")}
              </Link>
              <button
                onClick={() => askAgent(sessionId, drillQuestion(t, artifact.tile.title))}
                className="flex w-fit items-center gap-1.5 rounded-md border border-graphite-700 px-2 py-1 text-[12px] text-graphite-300 hover:bg-graphite-800 hover:text-graphite-50 transition"
                title={t("drill.hint")}
              >
                <Icon name="sparkle" size={13} />
                {t("drill.menu")}
              </button>
              <AnswerExport
                sessionId={sessionId}
                headline={artifact.tile.title}
                spec={artifact.tile.spec}
                resultTable={artifact.tile.result_table}
                compact
              />
            </div>
          )}
          <div className="min-h-0 flex-1">
            <DashboardTile
              tile={artifact.tile}
              large
              onElaborate={
                sessionId
                  ? (selection) =>
                      askAgent(sessionId, drillQuestion(t, artifact.tile.title, selection))
                  : undefined
              }
            />
          </div>
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
          <Icon name="chart" size={24} className="text-graphite-500" />
          <h1 className="text-lg font-medium text-graphite-100">{t("artifact.notFound")}</h1>
          <p className="max-w-sm text-[13px] text-graphite-400">{t("artifact.notFoundText")}</p>
        </div>
      )}
    </WorkspaceShell>
  );
}
