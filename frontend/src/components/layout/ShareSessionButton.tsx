"use client";

/**
 * Кнопка «Поделиться» в шапке сессии (#249): бандл бэкенда и лента из
 * браузера -> ссылка с данными. Окно показывает обе кодировки (замеры #244):
 * длину ссылки и влезает ли она в сообщение Telegram.
 */
import { useState } from "react";
import { getSessionBundle } from "@/api/client";
import { copyText } from "@/lib/clipboard";
import { compact, pack, shareUrl, TELEGRAM_LIMIT, type Encoding } from "@/lib/shareLink";
import { sessionSnapshot, useSessionStore } from "@/store/sessionStore";
import { useT } from "@/i18n/useT";
import Icon from "@/components/ui/Icon";

const ENCODINGS: Encoding[] = ["base64url", "base32768"];

type State =
  | { status: "closed" }
  | { status: "building" }
  | { status: "failed"; message: string }
  | { status: "ready"; links: Record<Encoding, string> };

export default function ShareSessionButton({ sessionId }: { sessionId: string }) {
  const t = useT();
  const [state, setState] = useState<State>({ status: "closed" });
  const [copied, setCopied] = useState<{ encoding: Encoding; ok: boolean } | null>(null);

  async function open() {
    setState({ status: "building" });
    setCopied(null);
    try {
      const bundle = await getSessionBundle(sessionId);
      const store = useSessionStore.getState();
      const packed = compact(bundle, store.sessionActiveTable[sessionId] ?? null, sessionSnapshot(store, sessionId));
      const encoded = await pack(packed);
      const origin = window.location.origin;
      setState({
        status: "ready",
        links: { base64url: shareUrl(origin, encoded.base64url), base32768: shareUrl(origin, encoded.base32768) },
      });
    } catch (err: any) {
      setState({ status: "failed", message: err?.detail || err?.message || String(err) });
    }
  }

  async function copy(encoding: Encoding, link: string) {
    setCopied({ encoding, ok: await copyText(link) });
  }

  const tooLong = state.status === "ready" && ENCODINGS.every((encoding) => state.links[encoding].length > TELEGRAM_LIMIT);

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
          <div className="w-full max-w-lg space-y-4 rounded-xl border border-graphite-700 bg-graphite-900 p-5 text-graphite-100 shadow-2xl">
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
                {ENCODINGS.map((encoding) => {
                  const link = state.links[encoding];
                  const fits = link.length <= TELEGRAM_LIMIT;
                  const mark = copied?.encoding === encoding ? copied : null;
                  return (
                    <div key={encoding} className="space-y-1.5 rounded-lg border border-graphite-800 bg-graphite-850 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[13px] text-graphite-100">{t(encoding === "base64url" ? "share.base64url" : "share.base32768")}</span>
                        <button
                          onClick={() => copy(encoding, link)}
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
                })}
                {tooLong && <p className="text-[12px] text-warn">{t("share.tooLong")}</p>}
                <p className="text-[12px] text-graphite-400">{t("share.telegram")}</p>
                <p className="text-[12px] text-graphite-400">{t("share.dataWarning")}</p>
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
