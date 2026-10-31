"use client";

import React, { useState } from "react";
import Icon from "@/components/ui/Icon";
import { copyText } from "@/lib/clipboard";

interface Props {
  content: string;
  className?: string;
}

/**
 * Парсер инлайн-разметки:
 * - **bold** / __bold__ -> <strong>
 * - *italic* / _italic_ -> <em>
 * - `code` -> <code>
 * - ~~strikethrough~~ -> <del>
 * - [text](url) -> <a>
 */
function renderInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  // Регулярное выражение для токенизации инлайн-разметки:
  // 1: `code`
  // 2: **bold** или __bold__
  // 3: *italic* или _italic_
  // 4: ~~strike~~
  // 5: [link](url)
  const regex = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|~~[^~]+~~|\[[^\]]+\]\([^)]+\))/g;

  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const token = match[0];
    const key = `${match.index}-${token.length}`;

    if (token.startsWith("`") && token.endsWith("`")) {
      nodes.push(
        <code
          key={key}
          className="rounded border border-graphite-700/60 bg-graphite-800 px-1.5 py-0.5 font-mono text-[12px] text-graphite-100"
        >
          {token.slice(1, -1)}
        </code>
      );
    } else if (
      (token.startsWith("**") && token.endsWith("**")) ||
      (token.startsWith("__") && token.endsWith("__"))
    ) {
      nodes.push(
        <strong key={key} className="font-semibold text-graphite-50">
          {renderInline(token.slice(2, -2))}
        </strong>
      );
    } else if (
      (token.startsWith("*") && token.endsWith("*")) ||
      (token.startsWith("_") && token.endsWith("_"))
    ) {
      nodes.push(
        <em key={key} className="italic text-graphite-200">
          {renderInline(token.slice(1, -1))}
        </em>
      );
    } else if (token.startsWith("~~") && token.endsWith("~~")) {
      nodes.push(
        <del key={key} className="line-through text-graphite-400">
          {renderInline(token.slice(2, -2))}
        </del>
      );
    } else if (token.startsWith("[")) {
      const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (linkMatch) {
        const [, label, url] = linkMatch;
        nodes.push(
          <a
            key={key}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-emerald-400 underline decoration-emerald-500/50 underline-offset-2 hover:text-emerald-300"
          >
            {label}
          </a>
        );
      } else {
        nodes.push(token);
      }
    } else {
      nodes.push(token);
    }

    lastIndex = regex.lastIndex;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes.length > 0 ? nodes : [text];
}

/** Блок кода с подсветкой и кнопкой копирования */
function CodeBlock({ code, language }: { code: string; language?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    const success = await copyText(code);
    if (success) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="group relative my-3 overflow-hidden rounded-xl border border-graphite-750 bg-graphite-950 shadow-sm">
      <div className="flex items-center justify-between border-b border-graphite-800 bg-graphite-900/80 px-3.5 py-1.5 text-xs text-graphite-400">
        <span className="font-mono text-[11px] font-medium uppercase tracking-wider text-graphite-300">
          {language || "code"}
        </span>
        <button
          onClick={handleCopy}
          className="inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[11px] text-graphite-400 transition hover:bg-graphite-800 hover:text-graphite-100"
          title="Копировать код"
        >
          <Icon name={copied ? "check" : "copy"} size={12} className={copied ? "text-emerald-400" : ""} />
          <span>{copied ? "Скопировано" : "Копировать"}</span>
        </button>
      </div>
      <pre className="overflow-x-auto p-3.5 font-mono text-[12.5px] leading-relaxed text-graphite-200">
        <code>{code}</code>
      </pre>
    </div>
  );
}

/** Таблица Markdown */
function MarkdownTable({ rows }: { rows: string[] }) {
  if (rows.length < 2) return null;

  // Разбор ячеек строки: | a | b | c |
  const parseRow = (line: string): string[] => {
    const trimmed = line.trim();
    const stripped = trimmed.replace(/^\|/, "").replace(/\|$/, "");
    return stripped.split("|").map((cell) => cell.trim());
  };

  const headerCells = parseRow(rows[0]);
  // Вторая строка — разделитель |---|---|
  const bodyRows = rows.slice(2).map(parseRow);

  return (
    <div className="my-3 overflow-x-auto rounded-lg border border-graphite-800 bg-graphite-900/60">
      <table className="min-w-full divide-y divide-graphite-800 text-left text-xs">
        <thead className="bg-graphite-850/90 text-graphite-200">
          <tr>
            {headerCells.map((cell, idx) => (
              <th key={idx} className="px-3.5 py-2 font-semibold">
                {renderInline(cell)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-graphite-800/60 text-graphite-300">
          {bodyRows.map((cells, rowIdx) => (
            <tr
              key={rowIdx}
              className="transition-colors odd:bg-graphite-900/40 even:bg-graphite-850/20 hover:bg-graphite-800/40"
            >
              {cells.map((cell, cellIdx) => (
                <td key={cellIdx} className="px-3.5 py-2">
                  {renderInline(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Основной компонент рендеринга Markdown:
 * Построчно группирует блоки (код, заголовки, списки, таблицы, цитаты, параграфы)
 * и транслирует в стилизованные компоненты React.
 */
export default function MarkdownContent({ content, className = "" }: Props) {
  if (!content) return null;

  const lines = content.split("\n");
  const elements: React.ReactNode[] = [];

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // 1. Блок кода: ```lang
    if (line.trim().startsWith("```")) {
      const lang = line.trim().slice(3).trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      elements.push(
        <CodeBlock key={`code-${i}`} code={codeLines.join("\n")} language={lang} />
      );
      i++;
      continue;
    }

    // 2. Таблица: начинается с | и следующая строка |---|
    if (
      line.trim().startsWith("|") &&
      i + 1 < lines.length &&
      lines[i + 1].trim().startsWith("|") &&
      lines[i + 1].includes("-")
    ) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        tableLines.push(lines[i]);
        i++;
      }
      elements.push(<MarkdownTable key={`table-${i}`} rows={tableLines} />);
      continue;
    }

    // 3. Заголовки #
    if (line.startsWith("#")) {
      const match = /^(#{1,6})\s+(.*)$/.exec(line);
      if (match) {
        const level = match[1].length;
        const headingText = match[2];
        const contentNode = renderInline(headingText);

        if (level === 1) {
          elements.push(
            <h1 key={`h1-${i}`} className="mt-3.5 mb-1.5 text-base font-semibold text-graphite-50">
              {contentNode}
            </h1>
          );
        } else if (level === 2) {
          elements.push(
            <h2 key={`h2-${i}`} className="mt-3 mb-1 text-sm font-semibold text-graphite-100">
              {contentNode}
            </h2>
          );
        } else {
          elements.push(
            <h3 key={`h3-${i}`} className="mt-2.5 mb-1 text-xs font-semibold uppercase tracking-wider text-graphite-300">
              {contentNode}
            </h3>
          );
        }
        i++;
        continue;
      }
    }

    // 4. Горизонтальная линия ---
    if (/^(\*{3,}|-{3,}|_{3,})$/.test(line.trim())) {
      elements.push(<hr key={`hr-${i}`} className="my-3 border-graphite-800" />);
      i++;
      continue;
    }

    // 5. Цитаты и Callouts: > ...
    if (line.startsWith(">")) {
      const quoteLines: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) {
        quoteLines.push(lines[i].replace(/^>\s?/, ""));
        i++;
      }
      const quoteText = quoteLines.join("\n");
      const isCallout =
        quoteText.includes("⚠️") ||
        quoteText.includes("ℹ️") ||
        quoteText.toLowerCase().includes("ограничен") ||
        quoteText.includes("[!NOTE]") ||
        quoteText.includes("[!WARNING]");

      elements.push(
        <div
          key={`quote-${i}`}
          className={`my-2.5 rounded-lg border-l-2 p-3 text-xs leading-relaxed ${
            isCallout
              ? "border-amber-500/70 bg-amber-500/10 text-amber-100"
              : "border-graphite-600 bg-graphite-850/60 text-graphite-300"
          }`}
        >
          {renderInline(quoteText)}
        </div>
      );
      continue;
    }

    // 6. Списки: маркированные (- / * / •) или нумерованные (1. / 2.)
    const isBullet = /^(\s*)([-*•]|\d+\.)\s+(.*)$/.exec(line);
    if (isBullet) {
      const listItems: { indent: number; text: string; ordered: boolean }[] = [];
      while (i < lines.length) {
        const itemMatch = /^(\s*)([-*•]|\d+\.)\s+(.*)$/.exec(lines[i]);
        if (!itemMatch) break;
        const indent = itemMatch[1].length;
        const isNum = /\d+\./.test(itemMatch[2]);
        listItems.push({ indent, text: itemMatch[3], ordered: isNum });
        i++;
      }

      elements.push(
        <ul key={`list-${i}`} className="my-2 space-y-1.5 pl-4 text-xs leading-relaxed text-graphite-200">
          {listItems.map((item, idx) => (
            <li
              key={idx}
              className={`flex items-start gap-2 ${item.indent > 0 ? "ml-4 text-graphite-300" : ""}`}
            >
              <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400/80" />
              <div className="flex-1">{renderInline(item.text)}</div>
            </li>
          ))}
        </ul>
      );
      continue;
    }

    // 7. Пустые строки
    if (!line.trim()) {
      i++;
      continue;
    }

    // 8. Обычный параграф
    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].startsWith("#") &&
      !lines[i].trim().startsWith("```") &&
      !lines[i].trim().startsWith("|") &&
      !lines[i].startsWith(">") &&
      !/^(\s*)([-*•]|\d+\.)\s+/.test(lines[i])
    ) {
      paraLines.push(lines[i]);
      i++;
    }

    elements.push(
      <p key={`p-${i}`} className="text-xs leading-relaxed text-graphite-200 mb-2 last:mb-0">
        {renderInline(paraLines.join(" "))}
      </p>
    );
  }

  return <div className={`markdown-content space-y-1 ${className}`}>{elements}</div>;
}
