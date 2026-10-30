"use client";

/**
 * Страница импорта сессии по ссылке (#249): `/import#v1.<…>`.
 * Данные берутся из фрагмента ссылки (на сервер он не уходит) или из
 * вставленного текста. Бандл уходит на бэкенд — там создаётся новая сессия;
 * ленту и папку «Импортированные» заполняет `openImported`.
 * При импорте ничего из ссылки не выполняется.
 */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { importSessionApi } from "@/api/client";
import WorkspaceShell from "@/components/layout/WorkspaceShell";
import { openImported } from "@/lib/importSession";
import { expand, ShareLinkError, unpack } from "@/lib/shareLink";
import { useT } from "@/i18n/useT";

export default function ImportPage() {
  const t = useT();
  const router = useRouter();
  const [text, setText] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  async function run(input: string) {
    setWorking(true);
    setError(null);
    try {
      const shared = expand(await unpack(input));
      const result = await importSessionApi(shared.bundle);
      openImported(shared, result, t);
      // Перезагрузка страницы не должна импортировать сессию второй раз
      window.history.replaceState(null, "", "/import");
      router.replace(`/session/${result.session_id}`);
    } catch (err: any) {
      if (err instanceof ShareLinkError) {
        setError(err.message === "version" ? t("import.version") : t("import.broken"));
      } else {
        setError(t("import.failed", { msg: err?.detail || err?.message || String(err) }));
      }
      setWorking(false);
    }
  }

  useEffect(() => {
    if (started.current || window.location.hash.length <= 1) return;
    started.current = true;
    run(window.location.href);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <WorkspaceShell tab={{ kind: "welcome" }}>
      <main className="flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-xl space-y-3">
          <h1 className="text-xl font-light text-graphite-50">{t("import.title")}</h1>
          <p className="text-[13px] text-graphite-400">{t("import.hint")}</p>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            disabled={working}
            placeholder="http://localhost:3000/import#v1.…"
            className="w-full resize-none rounded-lg border border-graphite-700 bg-graphite-850 px-3 py-2 font-mono text-[12px] text-graphite-100 outline-none focus:border-graphite-500"
          />
          <div className="flex items-center gap-3">
            <button
              onClick={() => run(text)}
              disabled={working || !text.trim()}
              className="rounded-lg bg-graphite-100 px-4 py-1.5 text-[13px] font-medium text-graphite-950 transition hover:bg-white disabled:opacity-40"
            >
              {t("import.open")}
            </button>
            {working && <span className="animate-pulse text-[13px] text-graphite-400">{t("import.working")}</span>}
          </div>
          {error && <p className="text-[13px] text-bad">{error}</p>}
        </div>
      </main>
    </WorkspaceShell>
  );
}
