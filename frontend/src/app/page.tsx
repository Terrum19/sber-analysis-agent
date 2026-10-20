"use client";

/** Главная страница: вкладка «Приветствие». */
import WorkspaceShell from "@/components/layout/WorkspaceShell";
import WelcomeScreen from "@/components/welcome/WelcomeScreen";

export default function HomePage() {
  return (
    <WorkspaceShell tab={{ kind: "welcome" }}>
      <WelcomeScreen />
    </WorkspaceShell>
  );
}
