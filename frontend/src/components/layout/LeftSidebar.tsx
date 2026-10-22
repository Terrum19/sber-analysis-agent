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
import { attachCsv, attachDb } from "@/api/client";
import { useSessionStore, type DatabaseFolder, type SessionItem } from "@/store/sessionStore";
import { useArtifactStore } from "@/store/artifactStore";
import { useUiStore } from "@/store/uiStore";
import { useStartSession } from "@/hooks/useStartSession";
import { useT } from "@/i18n/useT";
import { describeDatabase } from "@/lib/describeDatabase";
import Icon, { type IconName } from "@/components/ui/Icon";
import type { DbKind } from "@/types/datasource";
import ItemActionMenu from "./ItemActionMenu";

const DSN_PLACEHOLDER: Record<DbKind, string> = {
  postgres: "postgresql://analyst:pass@host:5432/db",
  clickhouse: "clickhouse://analyst:pass@host:8123/db",
};

function SidebarAction({
  icon,
  label,
  onClick,
  href,
  disabled,
}: {
  icon: IconName;
  label: string;
  onClick?: () => void;
  href?: string;
  disabled?: boolean;
}) {
  const className =
    "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] text-graphite-300 hover:bg-graphite-900 hover:text-graphite-100 transition disabled:opacity-50";
  const content = (
    <>
      <Icon name={icon} className="shrink-0 text-graphite-400" />
      <span className="truncate">{label}</span>
    </>
  );
  return href ? (
    <Link href={href} className={className}>
      {content}
    </Link>
  ) : (
    <button type="button" onClick={onClick} disabled={disabled} className={className}>
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

  const startSession = useStartSession();
  const isCurrent = useSessionStore((s) => (currentSessionId ? s.sessionDatabase[currentSessionId] === database.id : false));
  const description = describeDatabase(database, t);

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
          onClick={() => hasTables && startSession(database)}
          disabled={!hasTables}
          className="flex min-w-0 flex-1 items-start gap-2 py-1.5 text-left text-[13px] disabled:opacity-50 disabled:cursor-not-allowed"
          title={!hasTables ? t("welcome.noDataToStart") : `${t("sidebar.openNewSession", { title: database.title })}\n${description}`}
        >
          <Icon name={collapsed ? "folder" : "folderOpen"} className="mt-0.5 shrink-0 text-graphite-400" />
          <span className="min-w-0">
            <span className="flex items-center gap-1.5">
              <span className="block truncate">{database.title}</span>
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
        <button
          type="button"
          onClick={() => hasTables && startSession(database)}
          disabled={!hasTables}
          className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center self-start rounded-md text-graphite-500 hover:bg-graphite-800 hover:text-graphite-100 disabled:opacity-30 disabled:cursor-not-allowed"
          title={!hasTables ? t("welcome.noDataToStart") : t("sidebar.newSession")}
        >
          <Icon name="plus" size={14} />
        </button>
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
      </div>

      {!collapsed && (
        <div className="ml-[22px] border-l border-graphite-800 pl-1.5">
          {sortedSessions.length === 0 ? (
            <p className="px-2 py-1.5 text-[12px] text-graphite-500">{t("sidebar.noSessions")}</p>
          ) : (
            sortedSessions.map((s) => {
              const isActive = s.id === currentSessionId;
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
          onRename={(newTitle) => renameSession(menuAnchor.session.id, newTitle)}
          onDelete={() => onDeleteSession(menuAnchor.session.id)}
        />
      )}
    </div>
  );
}

/** Нижняя строка: тема и язык интерфейса. */
function PreferencesBar() {
  const t = useT();
  const theme = useUiStore((s) => s.theme);
  const setTheme = useUiStore((s) => s.setTheme);
  const locale = useUiStore((s) => s.locale);
  const setLocale = useUiStore((s) => s.setLocale);

  return (
    <div className="flex items-center justify-between gap-2 border-t border-graphite-800 px-3 py-2.5">
      <button
        type="button"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[12px] text-graphite-300 hover:bg-graphite-900 hover:text-graphite-100 transition"
        title={theme === "dark" ? t("sidebar.themeLight") : t("sidebar.themeDark")}
      >
        <Icon name={theme === "dark" ? "moon" : "sun"} />
        <span>{theme === "dark" ? t("sidebar.themeDark") : t("sidebar.themeLight")}</span>
      </button>

      <div
        role="radiogroup"
        aria-label={t("sidebar.language")}
        title={t("sidebar.language")}
        className="flex items-center rounded-lg border border-graphite-800 p-0.5 text-[11px] font-medium"
      >
        {(["ru", "en"] as const).map((l) => (
          <button
            key={l}
            type="button"
            role="radio"
            aria-checked={locale === l}
            onClick={() => setLocale(l)}
            className={`rounded-md px-2 py-0.5 uppercase transition ${
              locale === l ? "bg-graphite-800 text-graphite-50" : "text-graphite-500 hover:text-graphite-200"
            }`}
          >
            {l}
          </button>
        ))}
      </div>
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

  const dbModalOpen = useUiStore((s) => s.dbModalOpen);
  const setDbModalOpen = useUiStore((s) => s.setDbModalOpen);
  const csvPickRequest = useUiStore((s) => s.csvPickRequest);

  const sortedDatabases = useMemo(() => {
    return [...databases].sort((a, b) => {
      if (Boolean(a.pinned) === Boolean(b.pinned)) return 0;
      return a.pinned ? -1 : 1;
    });
  }, [databases]);

  function handleDeleteSession(sessionId: string) {
    const artStore = useArtifactStore.getState();
    const artIdsToRemove = Object.values(artStore.artifacts)
      .filter((a) => a.sessionId === sessionId)
      .map((a) => a.id);
    artStore.removeArtifactsBySessionIds([sessionId]);
    useUiStore.getState().closeTabsForSessions([sessionId], artIdsToRemove);
    deleteSession(sessionId);
    if (currentSessionId === sessionId) {
      router.push("/");
    }
  }

  function handleDeleteDatabase(databaseId: string) {
    const db = databases.find((d) => d.id === databaseId);
    const sessionIds = db ? db.sessions.map((s) => s.id) : [];
    const artStore = useArtifactStore.getState();
    const sSet = new Set(sessionIds);
    const artIdsToRemove = Object.values(artStore.artifacts)
      .filter((a) => a.sessionId && sSet.has(a.sessionId))
      .map((a) => a.id);
    artStore.removeArtifactsBySessionIds(sessionIds);
    useUiStore.getState().closeTabsForSessions(sessionIds, artIdsToRemove);
    deleteDatabase(databaseId);
    if (currentSessionId && sessionIds.includes(currentSessionId)) {
      router.push("/");
    }
  }

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dbKind, setDbKind] = useState<DbKind>("postgres");
  const [dsn, setDsn] = useState("");
  const [schema, setSchema] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Экран приветствия просит открыть выбор файлов
  useEffect(() => {
    if (csvPickRequest > 0) fileInputRef.current?.click();
  }, [csvPickRequest]);

  async function handleCsvUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length) return;

    setIsBusy(true);
    setErrorMsg(null);
    try {
      const ds = await attachCsv(files);
      const title = files.map((f) => f.name.replace(/\.csv$/i, "")).join(", ");
      const folder = addDatabase(ds, title);
      await startSession(folder);
    } catch (err: any) {
      setErrorMsg(err.message || t("sidebar.csvError"));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleDbConnect(e: React.FormEvent) {
    e.preventDefault();
    if (!dsn.trim()) return;

    setIsBusy(true);
    setErrorMsg(null);
    try {
      const ds = await attachDb({
        kind: dbKind,
        dsn: dsn.trim(),
        schema: dbKind === "postgres" ? schema.trim() : undefined,
      });
      const host = dsn.trim().split("@")[1]?.split("/")[0];
      const dbName = dsn.trim().split("/").pop()?.split("?")[0];
      const folder = addDatabase(ds, `${dbName || host || dbKind} (${dbKind === "postgres" ? "PostgreSQL" : "ClickHouse"})`);
      setDbModalOpen(false);
      setDsn("");
      setSchema("");
      await startSession(folder);
    } catch (err: any) {
      setErrorMsg(err.message || t("sidebar.dbError"));
    } finally {
      setIsBusy(false);
    }
  }

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
        <input ref={fileInputRef} type="file" accept=".csv,text/csv" multiple hidden onChange={handleCsvUpload} />
        <SidebarAction icon="upload" label={t("sidebar.uploadCsv")} onClick={() => fileInputRef.current?.click()} disabled={isBusy} />
        <SidebarAction
          icon="database"
          label={t("sidebar.connectDb")}
          onClick={() => {
            setErrorMsg(null);
            setDbModalOpen(true);
          }}
          disabled={isBusy}
        />
        <SidebarAction icon="layout" label={t("sidebar.allDashboards")} href="/dashboards" />
        {isBusy && <p className="px-2.5 pt-1 text-[11px] text-graphite-400 animate-pulse">{t("sidebar.processing")}</p>}
        {errorMsg && !dbModalOpen && <p className="px-2.5 pt-1 text-[11px] leading-tight text-bad">{errorMsg}</p>}
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

      {/* Модальное окно подключения БД */}
      {dbModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-md rounded-xl border border-graphite-700 bg-graphite-900 p-5 shadow-2xl text-graphite-100 space-y-4">
            <div className="flex items-center justify-between border-b border-graphite-800 pb-3">
              <h3 className="font-semibold text-sm">{t("modal.title")}</h3>
              <button
                onClick={() => setDbModalOpen(false)}
                className="text-graphite-400 hover:text-graphite-50"
                title={t("common.close")}
              >
                <Icon name="x" />
              </button>
            </div>
            <form onSubmit={handleDbConnect} className="space-y-3">
              <div>
                <label className="block text-xs text-graphite-400 mb-2">{t("modal.dbms")}</label>
                <div className="flex items-center gap-4">
                  <label className="flex items-center gap-2 cursor-pointer text-xs text-graphite-100">
                    <input
                      type="radio"
                      name="dbKind"
                      value="postgres"
                      checked={dbKind === "postgres"}
                      onChange={() => setDbKind("postgres")}
                      className="accent-graphite-100"
                    />
                    <span>PostgreSQL</span>
                  </label>
                  <label
                    className="flex items-center gap-2 cursor-not-allowed text-xs text-graphite-500"
                    title={t("modal.comingSoon")}
                  >
                    <input
                      type="radio"
                      name="dbKind"
                      value="clickhouse"
                      disabled
                      checked={false}
                      className="accent-graphite-100 cursor-not-allowed opacity-50"
                    />
                    <span>ClickHouse</span>
                    <span className="rounded bg-graphite-800 px-1.5 py-0.5 text-[10px] text-graphite-400 border border-graphite-700">
                      {t("modal.comingSoon")}
                    </span>
                  </label>
                </div>
              </div>
              <div>
                <label className="block text-xs text-graphite-400 mb-1">{t("modal.dsn")}</label>
                <input
                  type="text"
                  value={dsn}
                  onChange={(e) => setDsn(e.target.value)}
                  placeholder={DSN_PLACEHOLDER[dbKind]}
                  className="w-full rounded-lg border border-graphite-700 bg-graphite-950 px-3 py-2 text-xs text-graphite-50 placeholder-graphite-500 outline-none focus:border-graphite-400"
                  required
                />
              </div>
              {dbKind === "postgres" && (
                <div>
                  <label className="block text-xs text-graphite-400 mb-1">{t("modal.schema")}</label>
                  <input
                    type="text"
                    value={schema}
                    onChange={(e) => setSchema(e.target.value)}
                    placeholder="public"
                    className="w-full rounded-lg border border-graphite-700 bg-graphite-950 px-3 py-2 text-xs text-graphite-50 placeholder-graphite-500 outline-none focus:border-graphite-400"
                  />
                </div>
              )}
              {errorMsg && (
                <div className="rounded-lg border border-bad/30 bg-bad/10 p-2 text-xs text-bad">{errorMsg}</div>
              )}
              <div className="flex justify-end gap-2 pt-2 border-t border-graphite-800">
                <button
                  type="button"
                  onClick={() => setDbModalOpen(false)}
                  className="rounded-lg border border-graphite-700 px-3 py-1.5 text-xs text-graphite-300 hover:bg-graphite-800"
                >
                  {t("modal.cancel")}
                </button>
                <button
                  type="submit"
                  disabled={isBusy}
                  className="rounded-lg bg-graphite-100 px-4 py-1.5 text-xs font-medium text-graphite-950 hover:bg-graphite-50 disabled:opacity-50"
                >
                  {t("modal.connect")}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </aside>
  );
}
