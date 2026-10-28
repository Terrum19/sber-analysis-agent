"use client";

/**
 * Цитата ответа агента (F-12):
 * - composer — чип над полем ввода: иконка ответа, выдержка и «×»;
 * - message — компактная строка над отправленным вопросом в ленте; клик
 *   прокручивает к исходному ответу.
 */
import Icon from "@/components/ui/Icon";
import { useT } from "@/i18n/useT";

interface Props {
  excerpt: string;
  variant?: "composer" | "message";
  onClear?: () => void;
  onClick?: () => void;
}

export default function ReplyQuote({ excerpt, variant = "message", onClear, onClick }: Props) {
  const t = useT();
  const text = excerpt.trim() || "…";

  if (variant === "composer") {
    return (
      <div className="mb-2 flex items-start gap-2 rounded-xl border border-graphite-700 bg-graphite-850 px-3 py-2">
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-graphite-800 text-graphite-300">
          <Icon name="reply" size={13} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[10px] uppercase tracking-wider text-graphite-500">
            {t("chat.replyToTitle")}
          </span>
          <span className="mt-0.5 line-clamp-2 block text-[12px] leading-4 text-graphite-300" title={text}>
            {text}
          </span>
        </span>
        {onClear && (
          <button
            type="button"
            onClick={onClear}
            title={t("chat.clearReply")}
            aria-label={t("chat.clearReply")}
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-graphite-500 transition hover:bg-graphite-800 hover:text-graphite-100"
          >
            <Icon name="x" size={12} />
          </button>
        )}
      </div>
    );
  }

  const className =
    "mb-1 flex max-w-[85%] items-start gap-1.5 border-l-2 border-graphite-600 pl-2 text-left text-[11px] leading-4 text-graphite-400 transition";
  const content = (
    <>
      <Icon name="reply" size={11} className="mt-0.5 shrink-0 text-graphite-500" />
      <span className="line-clamp-2 italic">{text}</span>
    </>
  );

  if (!onClick) {
    return <div className={className}>{content}</div>;
  }

  return (
    <button
      type="button"
      onClick={onClick}
      title={t("chat.replyToTitle")}
      className={`${className} cursor-pointer hover:border-graphite-400 hover:text-graphite-200`}
    >
      {content}
    </button>
  );
}
