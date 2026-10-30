"use client";

/**
 * Кнопка «Поделиться» в шапке сессии (#249): бандл бэкенда и лента из
 * браузера -> ссылка с данными и ссылка-рецепт без точек (#250). Окно
 * показывает обе кодировки (замеры #244): длину ссылки и влезает ли она в
 * сообщение Telegram; у рецепта — сколько графиков всё же едут с данными
 * автора. Файл `.session` (#251) — бандл, лента и итоговые таблицы в parquet:
 * сохраняется по кнопке, предупреждение о данных — рядом с ней.
 */
import { useState } from "react";
import { exportSessionFile, getSessionBundle } from "@/api/client";
import { copyText } from "@/lib/clipboard";
import { downloadBlob, safeFilename } from "@/lib/download";
import { authorCharts, compact, fileFeed, pack, shareUrl, TELEGRAM_LIMIT, type Encoding } from "@/lib/shareLink";
import { sessionSnapshot, useSessionStore } from "@/store/sessionStore";
import { useT } from "@/i18n/useT";
import Icon from "@/components/ui/Icon";

const ENCODINGS: Encoding[] = ["base64url", "base32768"];

type Kind = "data" | "recipe";
type Links = Record<Encoding, string>;

type State =
  | { status: "closed" }
  | { status: "building" }
  | { status: "failed"; message: string }
  | { status: "ready"; links: Record<Kind, Links>; authorData: number; title: string };

type Saving = { status: "idle" } | { status: "saving" } | { status: "failed"; message: string };

export default function ShareSessionButton({ sessionId }: { sessionId: string }) {
  const t = useT();
  const [state, setState] = useState<State>({ status: "closed" });
  const [copied, setCopied] = useState<{ key: string; ok: boolean } | null>(null);
  const [saving, setSaving] = useState<Saving>({ status: "idle" });

  async function open() {
    setState({ status: "building" });
    setCopied(null);
    setSaving({ status: "idle" });
    try {
      const bundle = await getSessionBundle(sessionId);
      const store = useSessionStore.getState();
      const table = store.sessionActiveTable[sessionId] ?? null;
      const snapshot = sessionSnapshot(store, sessionId);
      const origin = window.location.origin;
      const links = async (recipe: boolean): Promise<Links> => {
        const encoded = await pack(compact(bundle, table, snapshot, recipe));
        return { base64url: shareUrl(origin, encoded.base64url), base32768: shareUrl(origin, encoded.base32768) };
      };
      setState({
        status: "ready",
        links: { data: await links(false), recipe: await links(true) },
        authorData: authorCharts(bundle, snapshot),
        title: bundle.manifest.title || t("share.fileName"),
      });
    } catch (err: any) {
      setState({ status: "failed", message: err?.detail || err?.message || String(err) });
    }
  }

  async function saveFile(title: string) {
    setSaving({ status: "saving" });
    try {
      const store = useSessionStore.getState();
      const blob = await exportSessionFile(sessionId, {
        table: store.sessionActiveTable[sessionId] ?? null,
        feed: fileFeed(sessionSnapshot(store, sessionId)),
      });
      downloadBlob(blob, safeFilename(title, "session"));
      setSaving({ status: "idle" });
    } catch (err: any) {
      setSaving({ status: "failed", message: err?.detail || err?.message || String(err) });
    }
  }

  async function copy(key: string, link: string) {
    setCopied({ key, ok: await copyText(link) });
  }

  const tooLong =
    state.status === "ready" && ENCODINGS.every((encoding) => state.links.data[encoding].length > TELEGRAM_LIMIT);

  function linkBlock(kind: Kind, encoding: Encoding, link: string) {
    const key = `${kind}-${encoding}`;
    const fits = link.length <= TELEGRAM_LIMIT;
    const mark = copied?.key === key ? copied : null;
    return (
      <div key={key} className="space-y-1.5 rounded-lg border border-graphite-800 bg-graphite-850 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[13px] text-graphite-100">
            {t(encoding === "base64url" ? "share.base64url" : "share.base32768")}
          </span>
          <button
            onClick={() => copy(key, link)}
            className="inline-flex items-center gap-1.5 rounded-md border border-graphite-700 px-2.5 py-1 text-[12px] text-graphite-200 transition hover:bg-graphite-800"
          >
            <Icon name={mark?.ok ? "check" : "copy"} size={13} />
            {mark ? (mark.ok ? t("share.copied") : t("share.copyFailed")) : t("share.copy")}
          </button>
        </div>
        <div className="text-[11.5px] text-graphite-400">
          {t("share.length", { n: link.length })} ·{" "}
          <span className={fits ? "text-good" : "text-warn"}>
            {fits ? t("share.fitsTelegram", { limit: TELEGRAM_LIMIT }) : t("share.mailOnly")}
          </span>
        </div>
        <div className="truncate font-mono text-[11px] text-graphite-500" title={link}>
          {link}
        </div>
      </div>
    );
  }

  return (
    <>
      <button
        onClick={open}
        className="flex h-7 w-7 items-center justify-center rounded-lg text-graphite-400 transition hover:bg-graphite-800 hover:text-graphite-100"
        title={t("share.button")}
      >
        <Icon name="share" />
      </button>

      {state.status !== "closed" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="max-h-[90vh] w-full max-w-lg space-y-4 overflow-y-auto rounded-xl border border-graphite-700 bg-graphite-900 p-5 text-graphite-100 shadow-2xl">
            <div className="flex items-center justify-between border-b border-graphite-800 pb-3">
              <h3 className="text-sm font-semibold">{t("share.title")}</h3>
              <button
                onClick={() => setState({ status: "closed" })}
                className="text-graphite-400 hover:text-graphite-50"
                title={t("common.close")}
              >
                <Icon name="x" />
              </button>
            </div>

            {state.status === "building" && (
              <p className="animate-pulse text-[13px] text-graphite-400">{t("share.building")}</p>
            )}
            {state.status === "failed" && (
              <p className="text-[13px] text-bad">{t("share.failed", { msg: state.message })}</p>
            )}
            {state.status === "ready" && (
              <>
                {ENCODINGS.map((encoding) => linkBlock("data", encoding, state.links.data[encoding]))}
                {tooLong && <p className="text-[12px] text-warn">{t("share.tooLong")}</p>}
                <p className="text-[12px] text-graphite-400">{t("share.dataWarning")}</p>

                <h4 className="border-t border-graphite-800 pt-3 text-[13px] font-semibold">{t("share.recipe")}</h4>
                {ENCODINGS.map((encoding) => linkBlock("recipe", encoding, state.links.recipe[encoding]))}
                <p className="text-[12px] text-graphite-400">{t("share.recipeHint")}</p>
                {state.authorData > 0 && (
                  <p className="text-[12px] text-warn">{t("share.recipeAuthorData", { n: state.authorData })}</p>
                )}

                <div className="flex items-center justify-between gap-2 border-t border-graphite-800 pt-3">
                  <h4 className="text-[13px] font-semibold">{t("share.file")}</h4>
                  <button
                    onClick={() => saveFile(state.title)}
                    disabled={saving.status === "saving"}
                    className="inline-flex items-center gap-1.5 rounded-md border border-graphite-700 px-2.5 py-1 text-[12px] text-graphite-200 transition hover:bg-graphite-800 disabled:opacity-50"
                  >
                    <Icon name="download" size={13} />
                    {saving.status === "saving" ? t("share.saving") : t("share.saveFile")}
                  </button>
                </div>
                <p className="text-[12px] text-graphite-400">{t("share.fileWarning")}</p>
                {saving.status === "failed" && (
                  <p className="text-[12px] text-bad">{t("share.saveFailed", { msg: saving.message })}</p>
                )}

                <p className="border-t border-graphite-800 pt-3 text-[12px] text-graphite-400">{t("share.telegram")}</p>
                <p className="text-[12px] text-graphite-500">
                  {t("share.howToOpen", { url: `${window.location.origin}/import` })}
                </p>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
