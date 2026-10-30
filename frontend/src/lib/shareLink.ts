/**
 * Ссылка с данными сессии (#249): бандл бэкенда + лента из браузера,
 * ужатые, сжатые deflate-raw и записанные текстом в фрагмент ссылки
 * `<origin>/import#v1.<…>`. Фрагмент на сервер не уходит.
 *
 * Перед сжатием выбрасывается то, что восстанавливается при распаковке:
 * строки данных хранятся по колонкам, у спек нет `$schema`, `width: "container"`
 * и таблицы результата автора, снимок плитки, совпадающий с точками спеки, —
 * пометка, плитка с графиком из ленты — ссылка на сообщение. Числа в данных
 * округляются до 6 значащих цифр (целая часть не теряется).
 *
 * Кодировок две, какая останется — по замерам (#244): base64url (ASCII,
 * кликается везде) и base32768 (символы Unicode, в 2,5 раза короче).
 * Распаковка узнаёт кодировку по символам.
 */
import { decode as decode32768, encode as encode32768 } from "base32768";
import type { ChatMessage, SessionSnapshot } from "@/store/sessionStore";

const VERSION = "v1.";
const VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json";
/** Колонки и строки таблицы данных. */
const COLS = "$t";
const ROWS = "$r";
/** Плитка дашборда рисует график сообщения ленты с этим номером. */
const MESSAGE = "$m";
/** answer.spec — тот же график, что chart.spec; snapshot_data — точки спеки. */
const SAME = 1;
/** Лимит сообщения Telegram в символах. */
export const TELEGRAM_LIMIT = 4096;

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type JsonObject = { [key: string]: Json };

export type Encoding = "base64url" | "base32768";

export interface SessionBundle {
  manifest: { format: number; title?: string | null; [key: string]: Json | undefined };
  source: JsonObject;
  turns: JsonObject[];
  memories: JsonObject[];
  dashboards: BundleDashboard[];
}

export interface BundleDashboard {
  id: string;
  title: string;
  template_id?: string | null;
  tiles: JsonObject[];
}

/** Распакованная ссылка: бандл для бэкенда и лента для браузера. */
export interface SharedSession {
  bundle: SessionBundle;
  /** Активная таблица автора: под ней лента лежит в кеше браузера. */
  table: string | null;
  messages: ChatMessage[];
}

export class ShareLinkError extends Error {}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function round(value: Json): Json {
  if (typeof value !== "number" || !Number.isFinite(value) || Number.isInteger(value)) return value;
  return Math.abs(value) >= 1e6 ? Math.round(value) : Number(value.toPrecision(6));
}

/** Список строк-объектов с одинаковыми ключами — по колонкам; иначе как есть. */
function packRows(rows: Json): Json {
  if (!Array.isArray(rows) || rows.length === 0 || !rows.every(isObject)) return rows;
  const cols = Object.keys(rows[0] as JsonObject);
  const same = rows.every((row) => {
    const keys = Object.keys(row as JsonObject);
    return keys.length === cols.length && cols.every((col) => col in (row as JsonObject));
  });
  if (!same) return rows;
  return { [COLS]: cols, [ROWS]: rows.map((row) => cols.map((col) => round((row as JsonObject)[col]))) };
}

function unpackRows(value: Json): Json {
  if (!isObject(value) || !Array.isArray(value[COLS]) || !Array.isArray(value[ROWS])) return value;
  const cols = value[COLS] as string[];
  return (value[ROWS] as Json[][]).map((row) => Object.fromEntries(cols.map((col, i) => [col, row[i] ?? null])));
}

function packSpec(spec: unknown): Json {
  if (!isObject(spec)) return (spec ?? null) as Json;
  const { $schema, width, usermeta, data, ...rest } = spec;
  const out: JsonObject = rest;
  if (width !== undefined && width !== "container") out.width = width;
  if (isObject(usermeta)) {
    const { result_table, ...meta } = usermeta;
    out.usermeta = meta;
  } else if (usermeta !== undefined) {
    out.usermeta = usermeta;
  }
  if (isObject(data) && "values" in data) out.data = { ...data, values: packRows(data.values) };
  else if (data !== undefined) out.data = data;
  return out;
}

function unpackSpec(spec: Json): Record<string, unknown> {
  if (!isObject(spec)) return {};
  const out: JsonObject = { ...spec };
  if ("mark" in out) {
    out.$schema = VEGA_LITE_SCHEMA;
    if (!("width" in out)) out.width = "container";
  }
  if (isObject(out.data) && "values" in out.data) out.data = { ...out.data, values: unpackRows(out.data.values) };
  return out;
}

/** JSON с отсортированными ключами: одинаковые спеки — одинаковая строка. */
function canonical(value: Json): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (isObject(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

/** Сообщение ленты для ссылки: текст, график, карточка дашборда, ответ; служебное — нет. */
function packMessage(m: ChatMessage): JsonObject | null {
  const content = m.content || m.clarification?.error || "";
  if (!content && !m.chart && !m.dashboard) return null;
  const out: JsonObject = { role: m.role, content };
  if (m.turn_id) out.turn_id = m.turn_id;
  if (m.reply_to) out.reply_to = { ...m.reply_to };
  if (m.timestamp) out.timestamp = m.timestamp;
  if (m.chart) out.chart = { caption: m.chart.caption ?? "", spec: packSpec(m.chart.spec) };
  if (m.dashboard) out.dashboard = { dashboard_id: m.dashboard.dashboard_id, title: m.dashboard.title ?? null };
  if (m.answer) {
    const answer: JsonObject = {};
    if (m.answer.headline) answer.headline = m.answer.headline;
    if (m.answer.details) answer.details = m.answer.details;
    if (m.answer.spec) answer.spec = m.chart ? SAME : packSpec(m.answer.spec);
    out.answer = answer;
  }
  return out;
}

function unpackMessage(m: JsonObject, index: number): ChatMessage {
  const chart = isObject(m.chart)
    ? { caption: String(m.chart.caption ?? ""), spec: unpackSpec(m.chart.spec), tile_id: `chart-imported-${index}` }
    : undefined;
  const answer = isObject(m.answer)
    ? {
        headline: typeof m.answer.headline === "string" ? m.answer.headline : undefined,
        details: Array.isArray(m.answer.details) ? (m.answer.details as string[]) : undefined,
        spec: m.answer.spec === SAME ? chart?.spec : m.answer.spec ? unpackSpec(m.answer.spec) : undefined,
      }
    : undefined;
  return {
    id: `msg-imported-${index}`,
    role: m.role === "user" ? "user" : "agent",
    content: String(m.content ?? ""),
    imported: true,
    ...(typeof m.turn_id === "string" ? { turn_id: m.turn_id } : {}),
    ...(isObject(m.reply_to) ? { reply_to: m.reply_to as unknown as ChatMessage["reply_to"] } : {}),
    ...(typeof m.timestamp === "string" ? { timestamp: m.timestamp } : {}),
    ...(chart ? { chart } : {}),
    ...(isObject(m.dashboard) ? { dashboard: m.dashboard as unknown as ChatMessage["dashboard"] } : {}),
    ...(answer ? { answer } : {}),
  };
}

function packTile(tile: JsonObject, charts: Map<string, number>): JsonObject {
  const { id, result_table, spec, snapshot_data, ...rest } = tile;
  const packed = packSpec(spec);
  const out: JsonObject = rest;
  const message = charts.get(canonical(packed));
  out.spec = message !== undefined ? { [MESSAGE]: message } : packed;
  const values = isObject(spec) && isObject(spec.data) ? spec.data.values : undefined;
  if (snapshot_data !== undefined && snapshot_data !== null) {
    out.snapshot_data =
      values !== undefined && canonical(snapshot_data) === canonical(values) ? SAME : packRows(snapshot_data);
  }
  return out;
}

function unpackTile(tile: JsonObject, messages: ChatMessage[]): JsonObject {
  const ref = isObject(tile.spec) ? tile.spec[MESSAGE] : undefined;
  const spec =
    typeof ref === "number" && messages[ref]?.chart
      ? (structuredClone(messages[ref].chart!.spec) as JsonObject)
      : (unpackSpec(tile.spec) as JsonObject);
  const out: JsonObject = { ...tile, spec };
  if (tile.snapshot_data === SAME) {
    out.snapshot_data = isObject(spec.data) ? (spec.data.values ?? null) : null;
  } else if (tile.snapshot_data !== undefined) {
    out.snapshot_data = unpackRows(tile.snapshot_data);
  }
  return out;
}

/** Бандл и лента -> ужатый JSON для сжатия. */
export function compact(bundle: SessionBundle, table: string | null, snapshot: SessionSnapshot): JsonObject {
  const feed = snapshot.messages.map(packMessage).filter((m): m is JsonObject => m !== null);
  const charts = new Map<string, number>();
  feed.forEach((m, index) => {
    if (isObject(m.chart)) charts.set(canonical(m.chart.spec), index);
  });
  return {
    ...(bundle as unknown as JsonObject),
    dashboards: bundle.dashboards.map((dashboard) => ({
      ...(dashboard as unknown as JsonObject),
      tiles: dashboard.tiles.map((tile) => packTile(tile, charts)),
    })),
    table,
    feed,
  };
}

/** Ужатый JSON -> бандл для бэкенда и лента. */
export function expand(packed: JsonObject): SharedSession {
  const feed = Array.isArray(packed.feed) ? packed.feed.filter(isObject) : [];
  const messages = feed.map(unpackMessage);
  const { feed: _feed, table, ...bundle } = packed;
  const dashboards = Array.isArray(bundle.dashboards) ? bundle.dashboards.filter(isObject) : [];
  return {
    bundle: {
      ...(bundle as unknown as SessionBundle),
      dashboards: dashboards.map((dashboard) => ({
        ...(dashboard as unknown as BundleDashboard),
        tiles: (Array.isArray(dashboard.tiles) ? dashboard.tiles.filter(isObject) : []).map((tile) =>
          unpackTile(tile, messages),
        ),
      })),
    },
    table: typeof table === "string" ? table : null,
    messages,
  };
}

async function pipe(
  bytes: Uint8Array<ArrayBuffer>,
  stream: CompressionStream | DecompressionStream,
): Promise<Uint8Array<ArrayBuffer>> {
  const output = new Blob([bytes]).stream().pipeThrough(stream);
  return new Uint8Array(await new Response(output).arrayBuffer());
}

function toBase64url(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64url(text: string): Uint8Array<ArrayBuffer> {
  const binary = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

/** Сжатый ужатый JSON в обеих кодировках: `v1.<…>` без origin. */
export async function pack(packed: JsonObject): Promise<Record<Encoding, string>> {
  const bytes = await pipe(new TextEncoder().encode(JSON.stringify(packed)), new CompressionStream("deflate-raw"));
  return { base64url: VERSION + toBase64url(bytes), base32768: VERSION + encode32768(bytes) };
}

/**
 * Ссылка или вставленный текст -> ужатый JSON. Браузер мог закодировать
 * символы Unicode как %XX, мессенджер — вставить переносы строк.
 */
export async function unpack(input: string): Promise<JsonObject> {
  let text = input.replace(/\s+/g, "");
  if (text.includes("%")) {
    try {
      text = decodeURIComponent(text);
    } catch {
      throw new ShareLinkError("broken");
    }
  }
  text = text.slice(text.indexOf("#") + 1);
  if (!text.startsWith(VERSION)) {
    throw new ShareLinkError(/^v\d+\./.test(text) ? "version" : "broken");
  }
  text = text.slice(VERSION.length);
  try {
    const bytes = /^[A-Za-z0-9_-]+$/.test(text) ? fromBase64url(text) : decode32768(text);
    const json = new TextDecoder().decode(await pipe(bytes, new DecompressionStream("deflate-raw")));
    const packed = JSON.parse(json);
    if (!isObject(packed)) throw new ShareLinkError("broken");
    return packed;
  } catch {
    throw new ShareLinkError("broken");
  }
}

/** Полная ссылка на страницу импорта этого приложения. */
export function shareUrl(origin: string, encoded: string): string {
  return `${origin}/import#${encoded}`;
}
