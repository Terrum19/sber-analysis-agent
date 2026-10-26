"use client";

/** Открывает плитку (график, KPI) на весь экран в отдельной вкладке. */
import { useRouter } from "next/navigation";
import { useArtifactStore } from "@/store/artifactStore";
import type { DashboardTile } from "@/types/dashboard";

export function useOpenArtifact() {
  const router = useRouter();
  const saveArtifact = useArtifactStore((s) => s.saveArtifact);

  return function openArtifact(tile: DashboardTile, sessionId?: string) {
    const id = saveArtifact(tile, sessionId);
    router.push(`/artifact/${id}`);
  };
}
