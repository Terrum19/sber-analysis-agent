"use client";

/**
 * Плашка ссылки-рецепта (#250) в импортированной сессии: графики пришли без
 * точек и ждут пересчёта. «Пересчитать» → POST /api/sessions/{id}/replay:
 * бэкенд ищет таблицу автора у получателя и выполняет шаги автора без LLM.
 * Базы автора у получателя нет — форма подключения с адресом из ссылки,
 * пароль вводит получатель, дальше обычный POST /api/datasources/db.
 * Автоматически ничего не запускается.
 */
import { useState } from "react";
import { attachDb, replaySessionApi } from "@/api/client";
import { chartTile } from "@/store/chatRunStore";
import { sessionSnapshot, useSessionStore, type ChatMessage, type SessionSnapshot } from "@/store/sessionStore";
import type { DbKind, ReplayResult, TableCheck } from "@/types/datasource";
import { useT, type Translate } from "@/i18n/useT";

type State =
  | { status: "idle" }
  | { status: "running" }
  | { status: "connect"; check: TableCheck }
  | { status: "message"; text: string; bad: boolean };

/** DSN из ссылки (пароль замаскирован) с паролем получателя. */
export function withPassword(dsn: string, password: string): string {
  const clean = dsn.trim().replace(":***@", "@");
  if (!password) return clean;
  return clean.replace(
    /^([a-z][a-z0-9+.-]*:\/\/)([^@/]*)@/i,
    (_, scheme: string, user: string) => `${scheme}${user}:${encodeURIComponent(password)}@`,
  );
}

function waiting(m: ChatMessage): boolean {
  return Boolean(m.chart && m.turn_id && (m.recipe === "pending" || m.recipe === "failed"));
}

/** Лента и плитки после пересчёта: новые спеки, карточки дашбордов, причины отказов. */
function applied(snap: SessionSnapshot, result: ReplayResult, fallback: string): SessionSnapshot {
  const dashboards = new Map(result.dashboards.map((d) => [d.dashboard_id, d]));
  let tiles = snap.tiles;
  const messages = snap.messages.map((m) => {
    const card = m.dashboard ? dashboards.get(m.dashboard.dashboard_id) : undefined;
    const next = card ? { ...m, dashboard: card } : m;
    if (!waiting(m)) return next;
    const chart = result.charts[m.turn_id!];
    if (!chart) {
      const error = result.failed[m.turn_id!];
      return error ? { ...next, recipe: "failed" as const, recipeError: error } : next;
    }
    const result_table = chart.result_table ?? undefined;
    const replayed: ChatMessage = {
      ...next,
      recipe: "replayed",
      recipeError: undefined,
      chart: { ...m.chart!, spec: chart.spec, result_table },
      ...(m.answer ? { answer: { ...m.answer, spec: chart.spec, result_table } } : {}),
    };
    const tile = chartTile(replayed.chart, fallback);
    tiles = tiles.some((x) => x.id === tile.id)
      ? tiles.map((x) => (x.id === tile.id ? { ...x, spec: tile.spec, result_table } : x))
      : [...tiles, tile];
    return replayed;
  });
  return { messages, tiles };
}

/** Почему пересчитать не по чему: текст для плашки. */
function blocked(check: TableCheck | null, t: Translate): string {
  if (!check) return t("recipe.noSource");
  const vars = { table: check.table, dsn: check.dsn ?? "", file: check.file ?? check.table };
  if (check.status === "different") return t("recipe.csvDifferent", vars);
  if (check.status === "missing") return t("recipe.csvMissing", vars);
  return t("recipe.noTable", vars);
}

export default function RecipeBanner({ sessionId, messages }: { sessionId: string; messages: ChatMessage[] }) {
  const t = useT();
  const followAgentTable = useSessionStore((s) => s.followAgentTable);
  const patchSnapshot = useSessionStore((s) => s.patchSnapshot);
  const addDatabase = useSessionStore((s) => s.addDatabase);
  const [state, setState] = useState<State>({ status: "idle" });
  const [dsn, setDsn] = useState("");
  const [schema, setSchema] = useState("");
  const [password, setPassword] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  const pending = messages.some(waiting);

  async function replay() {
    setState({ status: "running" });
    try {
      const snap = sessionSnapshot(useSessionStore.getState(), sessionId);
      const charts = Object.fromEntries(snap.messages.filter(waiting).map((m) => [m.turn_id!, m.chart!.spec]));
      const result = await replaySessionApi(sessionId, charts);
      const check = result.check;
      const computed = Object.keys(result.charts).length + Object.keys(result.failed).length > 0;
      if (computed) {
        if (check?.bound) followAgentTable(sessionId, check.bound);
        patchSnapshot(sessionId, (snapshot) => applied(snapshot, result, t("chat.chartDefault")));
      }
      if (!check?.bound && (check?.status === "not_connected" || !computed)) {
        if (check?.status === "not_connected") {
          setDsn((check.dsn ?? "").replace(":***@", "@"));
          setSchema(check.db_schema ?? "");
          setState({ status: "connect", check });
        } else {
          setState({ status: "message", text: blocked(check, t), bad: true });
        }
        return;
      }
      const failed = Object.keys(result.failed).length;
      setState({
        status: "message",
        text: t("recipe.done", { ok: Object.keys(result.charts).length, failed }),
        bad: failed > 0,
      });
    } catch (err: any) {
      setState({ status: "message", text: t("recipe.failed", { msg: err?.detail || err?.message || String(err) }), bad: true });
    }
  }

  async function connect(event: React.FormEvent, check: TableCheck) {
    event.preventDefault();
    const kind = check.kind as DbKind;
    setConnecting(true);
    setConnectError(null);
    try {
      const datasource = await attachDb({
        kind,
        dsn: withPassword(dsn, password),
        schema: kind === "postgres" ? schema.trim() || undefined : undefined,
      });
      const name = dsn.split("/").pop()?.split("?")[0] || kind;
      addDatabase(datasource, `${name} (${kind === "postgres" ? "PostgreSQL" : "ClickHouse"})`);
      setPassword("");
      await replay();
    } catch (err: any) {
      setConnectError(err?.message || String(err));
    } finally {
      setConnecting(false);
    }
  }

  if (!pending && state.status === "idle") return null;

  const input =
    "w-full rounded-md border border-graphite-700 bg-graphite-850 px-2.5 py-1.5 font-mono text-[12px] text-graphite-100 outline-none focus:border-graphite-500";

  return (
    <div className="mx-auto w-full max-w-3xl space-y-2 rounded-xl border border-graphite-700 bg-graphite-900 p-3 text-[13px] text-graphite-200">
      <div className="flex items-center justify-between gap-3">
        <span>
          {state.status === "message" ? (
            <span className={state.bad ? "text-warn" : "text-good"}>{state.text}</span>
          ) : (
            t("recipe.banner")
          )}
        </span>
        {pending && state.status !== "connect" && (
          <button
            onClick={replay}
            disabled={state.status === "running"}
            className="shrink-0 rounded-lg bg-graphite-100 px-3 py-1 text-[12.5px] font-medium text-graphite-950 transition hover:bg-white disabled:opacity-50"
          >
            {state.status === "running" ? t("recipe.running") : t("recipe.replay")}
          </button>
        )}
      </div>

      {state.status === "connect" && (
        <form onSubmit={(event) => connect(event, state.check)} className="space-y-2 border-t border-graphite-800 pt-2">
          <p className="text-[12.5px] text-graphite-300">{t("recipe.connectTitle", { table: state.check.table })}</p>
          <label className="block space-y-1">
            <span className="text-[11.5px] text-graphite-400">{t("recipe.address")}</span>
            <input value={dsn} onChange={(e) => setDsn(e.target.value)} className={input} />
          </label>
          {state.check.kind === "postgres" && (
            <label className="block space-y-1">
              <span className="text-[11.5px] text-graphite-400">{t("recipe.schema")}</span>
              <input value={schema} onChange={(e) => setSchema(e.target.value)} placeholder="public" className={input} />
            </label>
          )}
          <label className="block space-y-1">
            <span className="text-[11.5px] text-graphite-400">{t("recipe.password")}</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className={input}
            />
          </label>
          {connectError && <p className="text-[12px] text-bad">{connectError}</p>}
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={connecting || !dsn.trim()}
              className="rounded-lg bg-graphite-100 px-3 py-1 text-[12.5px] font-medium text-graphite-950 transition hover:bg-white disabled:opacity-50"
            >
              {connecting ? t("recipe.running") : t("recipe.connect")}
            </button>
            <button
              type="button"
              onClick={() => setState({ status: "idle" })}
              className="rounded-lg border border-graphite-700 px-3 py-1 text-[12.5px] text-graphite-300 hover:bg-graphite-800"
            >
              {t("recipe.cancel")}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
