"use client";

/**
 * Левая боковая панель:
 * 1. Загрузка CSV и подключение базы данных
 * 2. Вкладка реестра дашбордов
 * 3. Загруженные базы данных — папки, внутри которых создаются сессии
 *    (клик по названию или «+» открывает пустое окно новой сессии)
 * 4. Внизу — переключатели темы и языка
 */
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  deleteDashboard,
  deleteDatasourceApi,
  deleteSessionApi,
  fetchDashboards,
  listSessions,
  renameSessionApi,
  SESSIONS_PAGE,
} from "@/api/client";
import { useSessionStore, type DatabaseFolder, type SessionItem } from "@/store/sessionStore";
import { useCanvasStore } from "@/store/canvasStore";
import { forgetRuns, useChatRunStore } from "@/store/chatRunStore";
import { useArtifactStore } from "@/store/artifactStore";
import { tabHref, useUiStore } from "@/store/uiStore";
import { useStartSession } from "@/hooks/useStartSession";
import { useCsvUpload } from "@/hooks/useCsvUpload";
import { useSessionFileImport } from "@/hooks/useSessionFileImport";
import { useT } from "@/i18n/useT";
import { describeDatabase } from "@/lib/describeDatabase";
import Icon, { type IconName } from "@/components/ui/Icon";
import ItemActionMenu from "./ItemActionMenu";
import RecentDashboards from "./RecentDashboards";
import RunDot from "./RunDot";

function SidebarAction({
  icon,
  label,
  onClick,
  href,
  disabled,
  title,
}: {
  icon: IconName;
  label: string;
  onClick?: () => void;
  href?: string;
  disabled?: boolean;
  title?: string;
}) {
  const className = `flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] text-graphite-300 hover:bg-graphite-900 hover:text-graphite-100 transition disabled:opacity-50 ${
    disabled ? "pointer-events-none opacity-50 cursor-not-allowed" : ""
  }`;
  const content = (
    <>
      <Icon name={icon} className="shrink-0 text-graphite-400" />
      <span className="truncate">{label}</span>
    </>
  );
  return href ? (
    <Link
      href={disabled ? "#" : href}
      className={className}
      title={title}
      onClick={(e) => {
        if (disabled) e.preventDefault();
      }}
    >
      {content}
    </Link>
  ) : (
    <button type="button" onClick={onClick} disabled={disabled} title={title} className={className}>
      {content}
    </button>
  );
}

function DatabaseRow({
  database,
  currentSessionId,
  onDeleteSession,
  onDeleteDatabase,
}: {
  database: DatabaseFolder;
  currentSessionId?: string;
  onDeleteSession: (sessionId: string) => void;
  onDeleteDatabase: (databaseId: string) => void;
}) {
  const t = useT();
  const collapsed = useSessionStore((s) => Boolean(s.collapsedDatabases[database.id]));
  const toggleDatabase = useSessionStore((s) => s.toggleDatabase);
  const renameDatabase = useSessionStore((s) => s.renameDatabase);
  const togglePinDatabase = useSessionStore((s) => s.togglePinDatabase);
  const renameSession = useSessionStore((s) => s.renameSession);
  const togglePinSession = useSessionStore((s) => s.togglePinSession);

  /** Оптимистичное переименование: стор сразу, затем PATCH; при ошибке — откат. */
  async function handleRenameSession(session: SessionItem, newTitle: string) {
    const previousTitle = session.title;
    renameSession(session.id, newTitle);
    const optimisticTitle = newTitle.trim() || previousTitle;
    try {
      await renameSessionApi(session.id, optimisticTitle);
    } catch {
      // Откатываем, только если имя с тех пор не меняли ещё раз.
      const current = useSessionStore
        .getState()
        .databases.flatMap((d) => d.sessions)
        .find((x) => x.id === session.id);
      if (current?.title === optimisticTitle) {
        renameSession(session.id, previousTitle);
      }
    }
  }

  const startSession = useStartSession();
  const isCurrent = useSessionStore((s) => (currentSessionId ? s.sessionDatabase[currentSessionId] === database.id : false));
  const runs = useChatRunStore((s) => s.runs);
  const imported = database.kind === "imported";
  const title = imported ? t("sidebar.imported") : database.title;
  const description = imported ? t("sidebar.importedHint") : describeDatabase(database, t);

  const [menuAnchor, setMenuAnchor] = useState<
    | { type: "db"; rect: DOMRect }
    | { type: "session"; session: SessionItem; rect: DOMRect }
    | null
  >(null);

  const sortedSessions = useMemo(() => {
    return [...database.sessions].sort((a, b) => {
      if (Boolean(a.pinned) === Boolean(b.pinned)) return 0;
      return a.pinned ? -1 : 1;
    });
  }, [database.sessions]);

  const hasTables = Boolean(database.tables && database.tables.length > 0);

  return (
    <div>
      <div
        className={`group flex items-center gap-1 rounded-lg pr-1 transition hover:bg-graphite-900 ${
          isCurrent ? "text-graphite-100" : "text-graphite-300"
        }`}
      >
        <button
          type="button"
          onClick={() => toggleDatabase(database.id)}
          className="flex h-7 w-6 shrink-0 items-center justify-center self-start text-graphite-500 hover:text-graphite-200"
          title={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
        >
          <Icon name={collapsed ? "chevronRight" : "chevronDown"} size={14} />
        </button>
        <button
          type="button"
          onClick={() => (imported ? toggleDatabase(database.id) : hasTables && startSession(database))}
          disabled={!hasTables && !imported}
          className="flex min-w-0 flex-1 items-start gap-2 py-1.5 text-left text-[13px] disabled:opacity-50 disabled:cursor-not-allowed"
          title={
            imported
              ? description
              : !hasTables
              ? t("welcome.noDataToStart")
              : [
                  t("sidebar.openNewSession", { title: database.title }),
                  description,
                  database.fileName !== database.title ? database.fileName : null,
                ]
                  .filter(Boolean)
                  .join("\n")
          }
        >
          <Icon name={collapsed ? "folder" : "folderOpen"} className="mt-0.5 shrink-0 text-graphite-400" />
          <span className="min-w-0">
            <span className="flex items-center gap-1.5">
              <span className="block truncate">{title}</span>
              {database.pinned && (
                <Icon
                  name="pin"
                  size={11}
                  className="shrink-0 text-graphite-400"
                  title={database.kind === "csv" ? t("sidebar.pinCsv") : t("sidebar.pinDb")}
                />
              )}
            </span>
            <span className="block truncate text-[11px] leading-4 text-graphite-500">{description}</span>
          </span>
        </button>
        {!imported && (
        <button
          type="button"
          onClick={() => hasTables && startSession(database)}
          disabled={!hasTables}
          className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center self-start rounded-md text-graphite-500 hover:bg-graphite-800 hover:text-graphite-100 disabled:opacity-30 disabled:cursor-not-allowed"
          title={!hasTables ? t("welcome.noDataToStart") : t("sidebar.newSession")}
        >
          <Icon name="plus" size={14} />
        </button>
        )}
        {!imported && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            const rect = e.currentTarget.getBoundingClientRect();
            setMenuAnchor((prev) => (prev?.type === "db" ? null : { type: "db", rect }));
          }}
          className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center self-start rounded-md transition ${
            menuAnchor?.type === "db"
              ? "bg-graphite-800 text-graphite-100 opacity-100"
              : "text-graphite-500 hover:bg-graphite-800 hover:text-graphite-100 opacity-0 group-hover:opacity-100 focus:opacity-100"
          }`}
          title={t("sidebar.moreActions")}
        >
          <Icon name="moreHorizontal" size={14} />
        </button>
        )}
      </div>

      {!collapsed && (
        <div className="ml-[22px] border-l border-graphite-800 pl-1.5">
          {sortedSessions.length === 0 ? (
            <p className="px-2 py-1.5 text-[12px] text-graphite-500">{t("sidebar.noSessions")}</p>
          ) : (
            sortedSessions.map((s) => {
              const isActive = s.id === currentSessionId;
              const run = runs[s.id];
              const isSessionMenuOpen = menuAnchor?.type === "session" && menuAnchor.session.id === s.id;
              return (
                <div
                  key={s.id}
                  className={`group/session flex items-center justify-between rounded-lg pr-1 transition ${
                    isActive
                      ? "bg-graphite-800 text-graphite-50"
                      : "text-graphite-400 hover:bg-graphite-900 hover:text-graphite-200"
                  }`}
                >
                  <Link
                    href={`/session/${s.id}`}
                    className="flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1.5 text-[12.5px]"
                  >
                    <RunDot run={run} />
                    <span className="truncate">{s.title}</span>
                    {s.pinned && (
                      <Icon
                        name="pin"
                        size={11}
                        className="shrink-0 text-graphite-400"
                        title={t("sidebar.pinSession")}
                      />
                    )}
                  </Link>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      e.preventDefault();
                      const rect = e.currentTarget.getBoundingClientRect();
                      setMenuAnchor((prev) =>
                        prev?.type === "session" && prev.session.id === s.id
                          ? null
                          : { type: "session", session: s, rect },
                      );
                    }}
                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded transition ${
                      isSessionMenuOpen
                        ? "bg-graphite-700 text-graphite-100 opacity-100"
                        : "text-graphite-500 hover:bg-graphite-800 hover:text-graphite-100 opacity-0 group-hover/session:opacity-100 focus:opacity-100"
                    }`}
                    title={t("sidebar.moreActions")}
                  >
                    <Icon name="moreHorizontal" size={13} />
                  </button>
                </div>
              );
            })
          )}
        </div>
      )}

      {menuAnchor?.type === "db" && (
        <ItemActionMenu
          target={database.kind === "csv" ? "csv" : "db"}
          title={database.title}
          isPinned={Boolean(database.pinned)}
          anchorRect={menuAnchor.rect}
          onClose={() => setMenuAnchor(null)}
          onTogglePin={() => togglePinDatabase(database.id)}
          onRename={(newTitle) => renameDatabase(database.id, newTitle)}
          onDelete={() => onDeleteDatabase(database.id)}
        />
      )}

      {menuAnchor?.type === "session" && (
        <ItemActionMenu
          target="session"
          title={menuAnchor.session.title}
          isPinned={Boolean(menuAnchor.session.pinned)}
          anchorRect={menuAnchor.rect}
          onClose={() => setMenuAnchor(null)}
          onTogglePin={() => togglePinSession(menuAnchor.session.id)}
          onRename={(newTitle) => void handleRenameSession(menuAnchor.session, newTitle)}
          onDelete={() => onDeleteSession(menuAnchor.session.id)}
        />
      )}
    </div>
  );
}

/** Нижняя строка: тема оформления интерфейса. */
function PreferencesBar() {
  const t = useT();
  const theme = useUiStore((s) => s.theme);
  const setTheme = useUiStore((s) => s.setTheme);

  return (
    <div className="flex items-center justify-center border-t border-graphite-800 px-3 py-2.5">
      <button
        type="button"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        className="flex w-full items-center justify-center gap-2 rounded-lg px-2 py-1.5 text-[12px] text-graphite-300 hover:bg-graphite-900 hover:text-graphite-100 transition"
        title={theme === "dark" ? t("sidebar.themeLight") : t("sidebar.themeDark")}
      >
        <Icon name={theme === "dark" ? "moon" : "sun"} />
        <span>{theme === "dark" ? t("sidebar.themeDark") : t("sidebar.themeLight")}</span>
      </button>
    </div>
  );
}

export default function LeftSidebar() {
  const t = useT();
  const router = useRouter();
  const params = useParams();
  const currentSessionId = params?.id as string | undefined;

  const databases = useSessionStore((s) => s.databases);
  const addDatabase = useSessionStore((s) => s.addDatabase);
  const deleteDatabase = useSessionStore((s) => s.deleteDatabase);
  const deleteSession = useSessionStore((s) => s.deleteSession);
  const startSession = useStartSession();

  const setDbModalOpen = useUiStore((s) => s.setDbModalOpen);

  const sortedDatabases = useMemo(() => {
    return [...databases].sort((a, b) => {
      if (Boolean(a.pinned) === Boolean(b.pinned)) return 0;
      return a.pinned ? -1 : 1;
    });
  }, [databases]);

  async function handleDeleteSession(sessionId: string) {
    let dashboardIds: string[] = [];
    try {
      const allDashboards = await fetchDashboards();
      dashboardIds = allDashboards
        .filter((d) => d.session_id === sessionId)
        .map((d) => d.id);
    } catch {
      // Игнорируем сетевые ошибки
    }

    const artStore = useArtifactStore.getState();
    const artIdsToRemove = Object.values(artStore.artifacts)
      .filter((a) => a.sessionId === sessionId)
      .map((a) => a.id);
    artStore.removeArtifactsBySessionIds([sessionId]);
    const nextTab = useUiStore.getState().closeTabsForSessions([sessionId], artIdsToRemove, dashboardIds);

    const canvasStore = useCanvasStore.getState();
    if (
      (canvasStore.dashboardId && dashboardIds.includes(canvasStore.dashboardId)) ||
      useSessionStore.getState().sessionId === sessionId
    ) {
      canvasStore.clearCanvas();
    }

    forgetRuns([sessionId]);
    deleteSession(sessionId);
    Promise.allSettled([
      deleteSessionApi(sessionId),
      ...dashboardIds.map((did) => deleteDashboard(did)),
    ]).finally(() => {
      window.dispatchEvent(new Event("dashboards:changed"));
    });

    const isCurrentSession = currentSessionId === sessionId;
    const isCurrentDashboard = Boolean(params?.id && dashboardIds.includes(params.id as string));
    if (isCurrentSession || isCurrentDashboard) {
      router.push(nextTab ? tabHref(nextTab) : "/");
    }
  }

  async function handleDeleteDatabase(databaseId: string) {
    const db = databases.find((d) => d.id === databaseId);
    const sessionIds = db ? db.sessions.map((s) => s.id) : [];
    const sSet = new Set(sessionIds);

    let dashboardIds: string[] = [];
    try {
      const allDashboards = await fetchDashboards();
      dashboardIds = allDashboards
        .filter((d) => sSet.has(d.session_id))
        .map((d) => d.id);
    } catch {
      // Игнорируем сетевые ошибки
    }

    const artStore = useArtifactStore.getState();
    const artIdsToRemove = Object.values(artStore.artifacts)
      .filter((a) => a.sessionId && sSet.has(a.sessionId))
      .map((a) => a.id);
    artStore.removeArtifactsBySessionIds(sessionIds);
    const nextTab = useUiStore.getState().closeTabsForSessions(sessionIds, artIdsToRemove, dashboardIds);

    const canvasStore = useCanvasStore.getState();
    if (
      (canvasStore.dashboardId && dashboardIds.includes(canvasStore.dashboardId)) ||
      (currentSessionId && sSet.has(currentSessionId))
    ) {
      canvasStore.clearCanvas();
    }

    forgetRuns(sessionIds);
    deleteDatabase(databaseId);
    // Сначала сессии и дашборды источника, затем сам источник: его таблицы уходят из DuckDB,
    // список таблиц в шапке чата перечитывается и сайдбар обновляет дашборды.
    Promise.allSettled([
      ...sessionIds.map((sid) => deleteSessionApi(sid)),
      ...dashboardIds.map((did) => deleteDashboard(did)),
    ])
      .then(() => deleteDatasourceApi(databaseId))
      .catch(() => undefined)
      .finally(() => {
        useUiStore.getState().refreshTables();
        window.dispatchEvent(new Event("dashboards:changed"));
      });

    const isCurrentSessionDeleted = Boolean(currentSessionId && sSet.has(currentSessionId));
    const isCurrentDashboardDeleted = Boolean(params?.id && dashboardIds.includes(params.id as string));
    if (isCurrentSessionDeleted || isCurrentDashboardDeleted) {
      router.push(nextTab ? tabHref(nextTab) : "/");
    }
  }

  const {
    fileInputRef,
    isBusy: isCsvBusy,
    errorMsg: csvErrorMsg,
    handleCsvUpload,
    openFilePicker,
  } = useCsvUpload();
  const sessionFile = useSessionFileImport();
  const sessionFileRef = useRef<HTMLInputElement>(null);

  const isBusy = isCsvBusy;
  const errorMsg = csvErrorMsg;

  return (
    <aside className="flex h-full w-full flex-col bg-graphite-950 text-graphite-300 select-none">
      {/* Логотип */}
      <Link href="/" className="flex h-12 shrink-0 items-center gap-2 px-4 text-sm font-semibold text-graphite-100">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-graphite-700 text-xs font-bold text-graphite-50">
          S
        </span>
        <span>{t("brand")}</span>
      </Link>

      {/* Действия */}
      <div className="space-y-0.5 px-2 pb-3">
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,text/csv,.md,.txt"
          multiple
          hidden
          onChange={handleCsvUpload}
        />
        <SidebarAction
          icon="upload"
          label={t("sidebar.uploadCsv")}
          title={t("sidebar.csvHint")}
          onClick={openFilePicker}
          disabled={isBusy}
        />
        <SidebarAction
          icon="database"
          label={t("sidebar.connectDb")}
          onClick={() => {
            setDbModalOpen(true);
          }}
          disabled={isBusy}
        />
        <input
          ref={sessionFileRef}
          type="file"
          accept=".session"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file) sessionFile.importFile(file);
          }}
        />
        <SidebarAction
          icon="folder"
          label={t("sidebar.openSessionFile")}
          title={t("sidebar.openSessionFileHint")}
          onClick={() => sessionFileRef.current?.click()}
          disabled={isBusy || sessionFile.busy}
        />
        <SidebarAction
          icon="layout"
          label={t("sidebar.allDashboards")}
          href="/dashboards"
        />
        <RecentDashboards />
        {(isBusy || sessionFile.busy) && (
          <p className="px-2.5 pt-1 text-[11px] text-graphite-400 animate-pulse">{t("sidebar.processing")}</p>
        )}
        {sessionFile.error && <p className="px-2.5 pt-1 text-[11px] leading-tight text-bad">{sessionFile.error}</p>}
        {errorMsg && <p className="px-2.5 pt-1 text-[11px] leading-tight text-bad">{errorMsg}</p>}
      </div>

      {/* Загруженные базы данных и их сессии */}
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        <span className="block px-2.5 pb-1.5 text-[11px] font-medium text-graphite-500">{t("sidebar.databases")}</span>
        {sortedDatabases.length === 0 ? (
          <p className="px-2.5 text-[12px] text-graphite-500">{t("sidebar.noDatabases")}</p>
        ) : (
          <div className="space-y-0.5">
            {sortedDatabases.map((d) => (
              <DatabaseRow
                key={d.id}
                database={d}
                currentSessionId={currentSessionId}
                onDeleteSession={handleDeleteSession}
                onDeleteDatabase={handleDeleteDatabase}
              />
            ))}
          </div>
        )}
      </div>

      <PreferencesBar />
    </aside>
  );
}
