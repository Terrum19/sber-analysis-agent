"use client";

/**
 * Ответ агента в Markdown: абзацы, списки, **жирный**, *курсив*, `код`,
 * блоки кода с копированием, таблицы, цитаты, заголовки и ссылки.
 *
 * Текст приходит из LLM, в том числе кусками стрима, поэтому разбор терпит
 * незакрытую разметку (она показывается как есть, пока не придёт пара) и не
 * выводит HTML: всё идёт через React-узлы. Ссылки — только http(s) и mailto.
 * Подчёркивание разметкой не считается: в ответах много имён колонок
 * вроде order_id_total.
 */
import { useState, type ReactNode } from "react";
import Icon from "@/components/ui/Icon";
import { useT } from "@/i18n/useT";
import { copyText } from "@/lib/clipboard";

// `код` | **жирный** | *курсив* (без пробела у звёздочек: «2 * 3 * 4» — не курсив) | ~~зачёркнутый~~ | [текст](url)
const INLINE = /(`[^`\n]+`|\*\*(?!\s)[^*\n]+?\*\*|\*(?![\s*])[^*\n]+?(?<!\s)\*|~~[^~\n]+~~|\[[^\]\n]+\]\([^)\s]+\))/g;
const LINK = /^\[([^\]]+)\]\(([^)\s]+)\)$/;
const SAFE_URL = /^(https?:|mailto:)/i;
const LIST_ITEM = /^(\s*)([-*•]|\d+[.)])\s+(.*)$/;
const TABLE_DIVIDER = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const RULE = /^(\*{3,}|-{3,}|_{3,})$/;

function renderInline(text: string, keyPrefix = ""): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const token = match[0];
    const index = match.index ?? 0;
    const key = `${keyPrefix}${index}`;
    if (index > last) nodes.push(text.slice(last, index));
    last = index + token.length;
    if (token.startsWith("`")) {
      nodes.push(
        <code key={key} className="rounded bg-graphite-800 px-1 py-0.5 font-mono text-[0.9em] text-graphite-100">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith("**")) {
      nodes.push(
        <strong key={key} className="font-semibold text-graphite-50">
          {renderInline(token.slice(2, -2), `${key}-`)}
        </strong>,
      );
    } else if (token.startsWith("~~")) {
      nodes.push(
        <del key={key} className="text-graphite-400">
          {renderInline(token.slice(2, -2), `${key}-`)}
        </del>,
      );
    } else if (token.startsWith("*")) {
      nodes.push(<em key={key}>{renderInline(token.slice(1, -1), `${key}-`)}</em>);
    } else {
      const [, label, url] = LINK.exec(token) ?? [];
      nodes.push(
        url && SAFE_URL.test(url) ? (
          <a
            key={key}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-graphite-50 underline decoration-graphite-500 underline-offset-2 hover:decoration-graphite-300"
          >
            {label}
          </a>
        ) : (
          label ?? token
        ),
      );
    }
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (await copyText(code)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };
  return (
    <div className="overflow-hidden rounded-lg border border-graphite-800 bg-graphite-950">
      <div className="flex items-center justify-between border-b border-graphite-800 px-3 py-1 text-[11px] text-graphite-500">
        <span className="font-mono">{language || "code"}</span>
        <button onClick={copy} className="inline-flex items-center gap-1 hover:text-graphite-200">
          <Icon name={copied ? "check" : "copy"} size={12} className={copied ? "text-good" : ""} />
          {t(copied ? "step.copied" : "step.copy")}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 font-mono text-[12.5px] leading-relaxed text-graphite-200">
        <code>{code}</code>
      </pre>
    </div>
  );
}

function tableCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function Table({ header, rows }: { header: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-graphite-800">
      <table className="min-w-full text-left text-[13px]">
        <thead className="bg-graphite-850 text-graphite-100">
          <tr>
            {header.map((cell, i) => (
              <th key={i} className="px-3 py-1.5 font-semibold">
                {renderInline(cell)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-graphite-800">
          {rows.map((cells, r) => (
            <tr key={r}>
              {header.map((_, c) => (
                <td key={c} className="px-3 py-1.5 tabular-nums">
                  {renderInline(cells[c] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function List({ items }: { items: { indent: number; marker: string; text: string }[] }) {
  const ordered = /\d/.test(items[0].marker);
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag className={`space-y-1 pl-5 ${ordered ? "list-decimal" : "list-disc"} marker:text-graphite-500`}>
      {items.map((item, i) => (
        <li key={i} className={item.indent >= 2 ? "ml-4" : ""}>
          {renderInline(item.text)}
        </li>
      ))}
    </Tag>
  );
}

/** Строки -> блоки. Каждая ветка съедает хотя бы одну строку: незаконченный кусок стрима не зацикливает разбор. */
function parseBlocks(content: string): ReactNode[] {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();
    const key = `b${i}`;

    if (!trimmed) {
      i++;
      continue;
    }

    // Блок кода; закрывающей ``` ещё нет (идёт стрим) — до конца текста
    if (trimmed.startsWith("```")) {
      const language = trimmed.slice(3).trim();
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
      i++;
      blocks.push(<CodeBlock key={key} code={code.join("\n")} language={language} />);
      continue;
    }

    if (trimmed.startsWith("|") && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1].trim())) {
      const header = tableCells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(tableCells(lines[i++]));
      blocks.push(<Table key={key} header={header} rows={rows} />);
      continue;
    }

    const heading = HEADING.exec(trimmed);
    if (heading) {
      const Tag = heading[1].length <= 2 ? "h3" : "h4";
      blocks.push(
        <Tag key={key} className="font-semibold text-graphite-50">
          {renderInline(heading[2])}
        </Tag>,
      );
      i++;
      continue;
    }

    if (RULE.test(trimmed)) {
      blocks.push(<hr key={key} className="border-graphite-800" />);
      i++;
      continue;
    }

    if (trimmed.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) quote.push(lines[i++].trim().replace(/^>\s?/, ""));
      blocks.push(
        <blockquote key={key} className="border-l-2 border-graphite-600 pl-3 text-graphite-300">
          {renderInline(quote.join(" "))}
        </blockquote>,
      );
      continue;
    }

    if (LIST_ITEM.test(line)) {
      const items: { indent: number; marker: string; text: string }[] = [];
      while (i < lines.length) {
        const item = LIST_ITEM.exec(lines[i]);
        if (item) {
          items.push({ indent: item[1].length, marker: item[2], text: item[3] });
        } else if (lines[i].trim() && /^\s+/.test(lines[i]) && items.length > 0) {
          // Продолжение пункта с отступом
          items[items.length - 1].text += ` ${lines[i].trim()}`;
        } else {
          break;
        }
        i++;
      }
      blocks.push(<List key={key} items={items} />);
      continue;
    }

    // Абзац: первая строка — всегда его, дальше — до пустой строки или начала другого блока
    const paragraph = [trimmed];
    i++;
    while (i < lines.length) {
      const next = lines[i].trim();
      if (
        !next ||
        next.startsWith("```") ||
        next.startsWith(">") ||
        HEADING.test(next) ||
        LIST_ITEM.test(lines[i]) ||
        (next.startsWith("|") && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1].trim()))
      ) {
        break;
      }
      paragraph.push(next);
      i++;
    }
    blocks.push(
      <p key={key} className="whitespace-pre-line">
        {renderInline(paragraph.join("\n"))}
      </p>,
    );
  }
  return blocks;
}

export default function MarkdownContent({ content, className = "" }: { content: string; className?: string }) {
  if (!content) return null;
  return <div className={`space-y-2 break-words ${className}`}>{parseBlocks(content)}</div>;
}
