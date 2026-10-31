/**
 * Карточки вкладки «Инсайты» (F-17): вопрос пользователя → вывод агента.
 *
 * Ход агента в ленте — несколько сообщений: прогресс, график, текст; `answer`
 * и `turn_id` получает последнее. Поэтому сообщения собираем по ходам — всё
 * между двумя репликами пользователя. Плитки канваса, чей инсайт не встретился
 * в ленте (лента очищена, дашборд открыт из реестра), идут отдельными карточками.
 */
import type { ChatMessage } from "@/store/sessionStore";
import type { DashboardTile } from "@/types/dashboard";

export interface InsightCardData {
  key: string;
  /** Вопрос пользователя; у плитки без хода — её название. */
  question: string;
  /** Карточка собрана из плитки канваса, а не из ленты. */
  fromTile: boolean;
  headline?: string;
  details: string[];
  /** Инсайты плиток хода-дашборда. */
  tileInsights: { title: string; text: string }[];
  /** Графики хода — плитками, чтобы окно рисовало их как канвас. */
  charts: DashboardTile[];
  turnId?: string;
  time?: string;
  /** Для Excel-экспорта ответа (temp_result_vN). */
  resultTable?: string;
}

interface BuildOptions {
  /** Служебные тексты плиток, которые не выводы (`canvas.newKpiInsight` на обоих языках). */
  ignore?: string[];
  /** Название графика без подписи. */
  chartTitle: string;
}

const norm = (text: string) => text.replace(/\s+/g, " ").trim().toLowerCase();

const hasSpec = (spec: unknown) => Boolean(spec && typeof spec === "object" && Object.keys(spec).length > 0);

function chartOf(data: NonNullable<ChatMessage["chart"]>, fallback: string): DashboardTile {
  return {
    title: data.caption || fallback,
    spec: data.spec,
    result_table: data.result_table,
    layout: { x: 0, y: 0, w: 12, h: 5 },
  };
}

function turnCard(question: string, turn: ChatMessage[], index: number, opts: BuildOptions, ignore: Set<string>) {
  const answer = [...turn].reverse().find((m) => m.answer)?.answer;
  const dashboard = [...turn].reverse().find((m) => m.dashboard)?.dashboard;
  const dashTiles = dashboard?.tiles ?? [];

  const tileInsights = dashTiles
    .filter((tile) => tile.insight && !ignore.has(norm(tile.insight)))
    .map((tile) => ({ title: tile.title, text: tile.insight!.trim() }));
  const headline = answer?.headline?.trim() || (tileInsights.length > 0 ? dashboard?.title : undefined);
  const details = (answer?.details ?? []).map((d) => d.trim()).filter(Boolean);
  if (!headline && details.length === 0 && tileInsights.length === 0) return null;

  const charts: DashboardTile[] = [
    ...dashTiles.filter((tile) => hasSpec(tile.spec)),
    ...turn.flatMap((m) => (m.chart && hasSpec(m.chart.spec) ? [chartOf(m.chart, opts.chartTitle)] : [])),
  ];
  if (charts.length === 0 && answer?.spec && hasSpec(answer.spec)) {
    charts.push({ title: headline || opts.chartTitle, spec: answer.spec, layout: { x: 0, y: 0, w: 12, h: 5 } });
  }

  const last = [...turn].reverse().find((m) => m.timestamp);
  const turnId = [...turn].reverse().find((m) => m.turn_id)?.turn_id;
  const card: InsightCardData = {
    key: turnId ? `turn-${turnId}` : `msg-${index}`,
    question,
    fromTile: false,
    headline,
    details,
    tileInsights,
    charts,
    turnId,
    time: last?.timestamp,
    resultTable: answer?.result_table,
  };
  return card;
}

/** Карточки инсайтов, новые сверху; плитки без хода — в конце. */
export function buildInsights(
  messages: ChatMessage[],
  tiles: DashboardTile[],
  opts: BuildOptions,
): InsightCardData[] {
  const ignore = new Set((opts.ignore ?? []).map(norm));
  const cards: InsightCardData[] = [];
  let question = "";
  let turn: ChatMessage[] = [];
  let turnStart = 0;

  const flush = () => {
    if (turn.length > 0) {
      const card = turnCard(question, turn, turnStart, opts, ignore);
      if (card) cards.push(card);
    }
    turn = [];
  };

  messages.forEach((m, i) => {
    if (m.role === "user") {
      flush();
      question = m.content.trim();
      turnStart = i;
    } else {
      turn.push(m);
    }
  });
  flush();

  // Тексты, уже показанные в карточках ходов: такие плитки не дублируем
  const seen = new Set<string>();
  for (const card of cards) {
    if (card.headline) seen.add(norm(card.headline));
    card.details.forEach((d) => seen.add(norm(d)));
    card.tileInsights.forEach((t) => seen.add(norm(t.text)));
  }

  const orphans: InsightCardData[] = [];
  tiles.forEach((tile, i) => {
    const text = tile.insight?.trim();
    if (!text || ignore.has(norm(text)) || seen.has(norm(text))) return;
    seen.add(norm(text));
    orphans.push({
      key: `tile-${tile.id ?? i}`,
      question: tile.title,
      fromTile: true,
      headline: text,
      details: [],
      tileInsights: [],
      charts: hasSpec(tile.spec) ? [tile] : [],
      resultTable: tile.result_table,
    });
  });

  return [...cards.reverse(), ...orphans];
}

/** Строки текста карточки: вывод, детали, инсайты плиток. */
export function insightLines(card: InsightCardData): string[] {
  return [
    ...(card.headline ? [card.headline] : []),
    ...card.details,
    ...card.tileInsights.map((t) => `${t.title} — ${t.text}`),
  ];
}

/** Поиск по вопросу и тексту, без учёта регистра. */
export function filterInsights(cards: InsightCardData[], query: string): InsightCardData[] {
  const q = norm(query);
  if (!q) return cards;
  return cards.filter((card) => norm([card.question, ...insightLines(card)].join(" ")).includes(q));
}

export type TextPart = { text: string; kind: "plain" | "num" | "match" };

// Числа с разрядами через пробел («1 234 567»), дробью и процентом
const NUMBER = /[−-]?\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?(?:\s?%)?|[−-]?\d+(?:[.,]\d+)?(?:\s?%)?/g;

function splitNumbers(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let pos = 0;
  for (const m of text.matchAll(NUMBER)) {
    const start = m.index ?? 0;
    // Цифры внутри слова (temp_result_v3, Q4) — не значения
    if (start > 0 && /[\p{L}_]/u.test(text[start - 1])) continue;
    if (start > pos) parts.push({ text: text.slice(pos, start), kind: "plain" });
    parts.push({ text: m[0], kind: "num" });
    pos = start + m[0].length;
  }
  if (pos < text.length) parts.push({ text: text.slice(pos), kind: "plain" });
  return parts;
}

/** Текст на куски: совпадения поиска и числа подсвечиваются при просмотре. */
export function markText(text: string, query = ""): TextPart[] {
  const q = query.trim().toLowerCase();
  if (!q) return splitNumbers(text);
  const parts: TextPart[] = [];
  const lower = text.toLowerCase();
  let pos = 0;
  for (let at = lower.indexOf(q); at !== -1; at = lower.indexOf(q, pos)) {
    if (at > pos) parts.push(...splitNumbers(text.slice(pos, at)));
    parts.push({ text: text.slice(at, at + q.length), kind: "match" });
    pos = at + q.length;
  }
  if (pos < text.length) parts.push(...splitNumbers(text.slice(pos)));
  return parts;
}

/** Выгрузка карточек в Markdown в том же порядке, что на вкладке. */
export function insightsMarkdown(
  cards: InsightCardData[],
  labels: { title: string; chart: string },
): string {
  const out = [`# ${labels.title}`, ""];
  for (const card of cards) {
    out.push(`## ${card.question.replace(/\s+/g, " ")}`, "");
    if (card.time) out.push(`_${card.time}_`, "");
    if (card.headline) out.push(card.headline, "");
    const bullets = [...card.details, ...card.tileInsights.map((t) => `**${t.title}** — ${t.text}`)];
    if (bullets.length > 0) out.push(...bullets.map((b) => `- ${b}`), "");
    if (card.charts.length > 0) out.push(`${labels.chart}: ${card.charts.map((c) => c.title).join(", ")}`, "");
  }
  return out.join("\n").trimEnd() + "\n";
}
