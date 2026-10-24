# План реализации F-06, F-07, F-08

**Ветка:** `feature/F-06-F-08-dashboard-ui`
**База:** `feature/F-01-frontend-ui` (HEAD `30fab3f`)
**Создана:** 24.10.2026
**Статус:** план, код ещё не менялся

Порядок работ выбран по зависимостям из roadmap:

```
B-05 (API дашбордов) ──> F-07 ──> F-08
B-06 (SSE с плитками) ─> F-06 ──┘
```

F-07 делаем первой: на неё опирается сохранение раскладки F-08.
F-06 можно вести параллельно после проверки контракта B-06.
F-08 — последней, когда страницы и API уже стабильны.

---

## 1. Аудит: что уже есть в ветке

### F-06 · Превью дашборда в чате

| Готово | Где | Комментарий |
|---|---|---|
| ✅ | `ChatPanel.tsx:273-285` | событие `dashboard` разбирается, сообщение сохраняется в `sessionStore`, `fetchDashboard` не вызывается |
| ✅ | `ChatPanel.tsx:521-554` | у сообщения уже есть карточка-ссылка и кнопки «на панель» / «во вкладку» |
| ✅ | `ChatPanel.tsx:327-338` | `handleOpenDashboard` → `/dashboard/[id]`, `handleLoadToCanvas` → `canvasStore` |
| ✅ | `DashboardTile.tsx` | есть `mini` и `large`, график/KPI и инсайт рендерятся |
| ❌ | — | компактной сетки плиток в ленте чата нет: карточка показывает только название |
| ❌ | `DashboardTile.tsx` | нет режима `preview` |
| ❌ | `lib/tileLayout.ts` | нет раскладки для компактного превью в чате |
| ⚠️ | `backend/app/core/agent/orchestrator.py:284` | событие отправляет только `dashboard_id`, без `title/tiles`; `DashboardPayload.from_dashboard` уже написан, но не используется |

**Блокер:** без плиток в SSE F-06 нельзя выполнить без `GET`. Нужно либо
дождаться B-06, либо на этой ветке внести трёхстрочную правку в orchestrator
(см. §4.1). Это единственная backend-точка в плане.

### F-07 · Страницы дашбордов на реальном API

| Готово | Где | Комментарий |
|---|---|---|
| ✅ | `api/client.ts:336-446` | пути, методы и `ApiError` уже соответствуют B-05 |
| ✅ | `api/client.ts:407-429` | DELETE корректно обрабатывает `204` и пустое тело |
| ✅ | `app/dashboard/[id]/page.tsx` | загрузка, заголовок, «Обновить данные», пометка `stale`, экран «не найден» |
| ✅ | `app/dashboards/page.tsx` | список `GET /api/dashboards`, удаление через `confirm()` |
| ✅ | `DashboardTile.tsx:240-247` | бейдж «Снимок» для `stale: true` |
| ⚠️ | `dashboard/[id]/page.tsx:240` | «Снимок на …» берётся из `updated_at/created_at`, а не из `snapshot_at` плиток |
| ⚠️ | `dashboard/[id]/page.tsx:61-63,185` | любой сетевой ошибкой показывается экран «не найден»; нет отличия 404 от 5xx и нет повтора |
| ❌ | `LeftSidebar.tsx:516-522` | есть только ссылка «Все дашборды», последних 5 дашбордов нет |
| ⚠️ | `dashboards/page.tsx:69-76` | N+1 запросов за конфигами ради превью; нет защиты от гонок при размонтировании |

### F-08 · Канвас: drag&drop, resize, сохранение раскладки

| Готово | Где | Комментарий |
|---|---|---|
| ✅ | `package.json` | `react-grid-layout` и `@types/react-grid-layout` уже установлены |
| ✅ | `Canvas.tsx:383-410` | сетка 12 колонок, `rowHeight = 60 * zoom`, `x/y/w/h` из API |
| ✅ | `Canvas.tsx:78-96` | `onLayoutChange` обновляет layout в сторе |
| ✅ | `canvasStore.ts:71-74` | `updateTileLayout(index, layout)` уже есть |
| ❌ | `Canvas.tsx` | используется `GridLayout`, а не `ResponsiveGridLayout` |
| ❌ | `Canvas.tsx` | ручное сохранение; нет debounce 800 мс и статуса «Сохранено / Сохраняю / Ошибка» |
| ❌ | `ItemActionMenu.tsx` | меню умеет только `session | csv | db`; действий плитки (в т.ч. смены типа графика) нет |
| ⚠️ | `lib/tileLayout.ts:9` | `MIN_ROWS = { kpi: 3, chart: 4 }`, `minW: 3` — не совпадает с требованием KPI 2×2, график 4×3 |
| ⚠️ | `Canvas.tsx:70` | при отсутствии `dashboardId` плитки берутся из `sessionStore`; автосохранение должно учитывать этот режим |

---

## 2. Контракты, на которые опираемся

### 2.1 REST (B-05)

| Метод | Путь | Успех | Ошибка |
|---|---|---|---|
| GET | `/api/dashboards?session_id=` (опц.) | `200`, `DashboardSummary[]` по `updated_at desc` | — |
| GET | `/api/dashboards/{id}` | `200`, `DashboardOut` | `404 {"detail":"Дашборд не найден"}` |
| POST | `/api/dashboards` | `201`, `DashboardOut` | `422` |
| PUT | `/api/dashboards/{id}` | `200`, `DashboardOut` | `404` |
| DELETE | `/api/dashboards/{id}` | `204`, пустое тело | `404` |
| POST | `/api/dashboards/{id}/recalculate` | `200`, `DashboardOut` | `404` |

`DashboardTileOut` (важные поля): `id`, `title`, `spec`, `layout {x,y,w,h}`,
`source_step_id`, `result_table`, `insight`, `is_kpi`, `snapshot_data`,
`snapshot_at` (unix-секунды), `stale` (только в ответе API).
`DashboardOut` не содержит dashboard-уровневого `snapshot_at` — дата снимка
вычисляется как максимум `tile.snapshot_at`, fallback `updated_at`.

### 2.2 SSE-событие `dashboard` (B-06)

Целевой формат:

```json
{
  "type": "dashboard",
  "data": {
    "dashboard_id": "a1b2c3d4e5f6",
    "title": "Дашборд",
    "tiles": [
      {
        "id": "tile-1",
        "title": "Выручка по регионам",
        "spec": { "mark": "bar", "data": { "values": [] }, "usermeta": { "chart_type": "bar" } },
        "layout": { "x": 0, "y": 0, "w": 12, "h": 5 },
        "insight": "Москва лидирует",
        "is_kpi": false
      }
    ]
  }
}
```

Правила:
- `snapshot_data` в SSE не передаётся — данные уже в `spec.data.values`;
- после события **никаких** `GET /api/dashboards/{id}` для отрисовки;
- если по старой версии бэкенда `tiles` пуст — показываем карточку с кнопками,
  но не делаем запрос.

### 2.3 Тип графика

`backend/app/core/viz/vega_spec_builder.py`:

```python
CHART_TYPES = ("bar", "line", "area", "scatter", "histogram", "box", "heatmap", "table", "kpi")
```

`recalculate.rebuild_spec` берёт `usermeta.requested_type or usermeta.chart_type`.
Поэтому при смене типа на клиенте нужно выставлять **оба** поля, иначе
пересчёт вернёт старый тип. `is_kpi` обязан совпадать с `chart_type == "kpi"`.

---

## 3. F-07 · Страницы дашбордов на реальном API

**Цель:** дашборд, созданный в чате, открывается после перезагрузки страницы и
после рестарта бэкенда; список, удаление и дата снимка работают на реальном API.

### 3.1. `frontend/src/types/dashboard.ts`

- Добавить в `DashboardConfig` необязательное `snapshot_at?: number | null`
  (на случай, если B-05 добавит поле на уровне дашборда).
- Добавить хелперы (или отдельный `lib/dashboard.ts`, чтобы не смешивать типы и логику):

```ts
export function toDate(value?: number | string | null): Date | null;
export function dashboardSnapshotAt(config: DashboardConfig): number | null;
// max(tile.snapshot_at), fallback config.updated_at -> created_at
```

- `DashboardSummary` уже содержит `updated_at`, `tiles_count` — используем в списке.

### 3.2. `frontend/src/api/client.ts`

Методы уже соответствуют контракту; правки минимальные:

1. Оставить `fetchDashboard` как есть: при 404 он бросает `ApiError` со `status = 404`.
2. Добавить экспортируемый предикат:

```ts
export function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}
```

3. `deleteDashboard` не менять — 204 уже обработан.
4. Для `fetchDashboards` добавить опциональный `signal?: AbortSignal`, чтобы
   список в сайдбаре и на странице мог отменять устаревший запрос:

```ts
fetchDashboards(sessionId?: string, signal?: AbortSignal): Promise<DashboardListItem[]>
```

### 3.3. `frontend/src/app/dashboard/[id]/page.tsx`

1. **Разделить состояния ошибки.**
   - `notFound: boolean` — только для `isNotFound(err)`;
   - `error: string | null` — для сети/5xx;
   - экран «не найден» оставить с кнопкой «Все дашборды»;
   - для остальных ошибок — текст ошибки + кнопка «Повторить» (`load()`).
2. **Дата снимка.**
   - заменить `config.updated_at || config.created_at` на
     `dashboardSnapshotAt(config)`;
   - подпись: `t("dashboard.snapshotAt", { date: formatDate(...) })`;
   - если снимка нет — `—`, а не текущая дата.
3. **`stale`-плитки.**
   - бейдж уже есть в `DashboardTile`;
   - дополнительно в шапке показать счётчик: «N плиток с устаревшими данными»
     (i18n `dashboard.staleCount`), чтобы пользователь видел проблему без
     прокрутки.
4. **«Обновить данные».**
   - после `recalculateDashboard` заменить `config` целиком (уже есть);
   - обновить `snapshot_at` из ответа;
   - при 404 перевести страницу в `notFound`, при прочих ошибках — `flash`.
5. **Сохранение раскладки.**
   - `handleLayoutChange` уже пишет `{x,y,w,h}` в `config`;
   - после F-08 переиспользовать `tileGridLayout` с новыми минимальными
     размерами и `rowHeight = 60 * zoom`;
   - `handleRenameTile`/`handleDeleteTile` оставить, но действия плитки
     продублировать через `ItemActionMenu` (см. §5.5).
6. **Сброс состояния при смене `params.id`.**
   - в начале `load()` делать `setConfig(null)`, `setNotFound(false)`, `setError(null)`,
     чтобы при переходе между дашбордами не мигала старая конфигурация.

### 3.4. `frontend/src/app/dashboards/page.tsx`

1. **Удаление.**
   - `confirm(t("registry.deleteConfirm"))` оставить (требование «с подтверждением»);
   - после `deleteDashboard` удалять и `dashboards`, и `configs[id]`;
   - при 404 считать дашборд уже удалённым и убирать из списка;
   - при ошибке — `alert` с `registry.deleteError` (как сейчас).
2. **Список.**
   - показывать `dash.tiles_count` и дату `updated_at` (сейчас дата есть,
     число плиток — нет);
   - `fetchDashboards` вызывать с `AbortController`; в `finally` не обновлять
     состояние, если компонент размонтирован.
3. **Превью.**
   - оставить N+1-загрузку конфигов, но обернуть в `Promise.allSettled` и
     ограничить параллелизм (например, по 4), чтобы 20 дашбордов не открывали
     20 запросов одновременно;
   - ошибку отдельного превью не показывать — только скелетон/«нет плиток».
4. **Переход.**
   - `router.push('/dashboard/' + id)` уже есть; дополнительно `prefetch` не нужен.

### 3.5. `frontend/src/components/layout/RecentDashboards.tsx` (новый)

Вынести блок в отдельный компонент, чтобы не раздувать `LeftSidebar.tsx`.

```tsx
export default function RecentDashboards() {
  const pathname = usePathname();
  const canvasDashboardId = useCanvasStore((s) => s.dashboardId);
  const [items, setItems] = useState<DashboardListItem[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    fetchDashboards(undefined, controller.signal)
      .then((list) =>
        setItems(
          [...list]
            .sort((a, b) => Number(b.updated_at ?? 0) - Number(a.updated_at ?? 0))
            .slice(0, 5),
        ),
      )
      .catch(() => undefined); // сайдбар не должен ломать приложение
    return () => controller.abort();
  }, [pathname, canvasDashboardId]);

  // ... ссылки /dashboard/{id}
}
```

Требования:
- заголовок «Последние дашборды» (`sidebar.recentDashboards`);
- пустое состояние не показывать (или `sidebar.noDashboards`);
- каждая строка: иконка `layout`, `title` с `truncate`, `title`-tooltip;
- активная строка при `pathname === "/dashboard/" + id` — фон `graphite-800`;
- после удаления на `/dashboards` обновлять список: в `handleDelete` вызвать
  `window.dispatchEvent(new Event("dashboards:changed"))`, а компонент слушает
  это событие; альтернатива — общий zustand-store, но событие дешевле;
- после создания дашборда в чате список обновится по изменению
  `canvasDashboardId` без дополнительного `GET` из `ChatPanel`.

В `LeftSidebar.tsx`:
- импортировать `RecentDashboards`;
- вставить `<RecentDashboards />` сразу под `SidebarAction` «Все дашборды»;
- оставить существующую ссылку `/dashboards` без изменений.

### 3.6. i18n (`frontend/src/i18n/dictionary.ts`)

Добавить в `ru` и `en`:

| Ключ | RU | EN |
|---|---|---|
| `sidebar.recentDashboards` | Последние дашборды | Recent dashboards |
| `sidebar.noDashboards` | Дашбордов пока нет | No dashboards yet |
| `dashboard.staleCount` | Плиток с устаревшими данными: {n} | Tiles with stale data: {n} |
| `dashboard.retry` | Повторить загрузку | Retry loading |
| `dashboard.snapshotMissing` | Снимок ещё не сделан | No snapshot yet |

### 3.7. Проверка F-07

```bash
cd frontend
npx tsc --noEmit
npm run lint
npm run build
```

Ручной сценарий:
1. `npm run dev:mock` → создать дашборд в чате → открыть `/dashboard/{id}`.
2. Перезагрузить страницу (F5) — дашборд и плитки на месте.
3. Перезапустить backend (или `docker compose restart backend`) — открыть тот
   же URL: данные из SQLite, снимок и `snapshot_at` на месте.
4. Открыть несуществующий `/dashboard/does-not-exist` — экран «не найден»,
   кнопка ведёт на `/dashboards`.
5. На `/dashboards` удалить дашборд → подтверждение → карточка исчезает,
   в сайдбаре её тоже нет.
6. Сайдбар: видны последние 5, порядок по `updated_at`, клик открывает нужный.

**Готово, когда:** дашборд из чата открывается после F5 и рестарта бэкенда;
404 и сетевые ошибки различаются; в сайдбаре есть 5 последних дашбордов.

---

## 4. F-06 · Превью дашборда в чате из SSE

**Цель:** событие `dashboard` сразу рисует компактную сетку плиток в ленте,
кнопки ведут на полную страницу и на канвас; никакого `GET` после события.

### 4.1. Предусловие B-06 (backend)

Сейчас `orchestrator.py:284` отправляет:

```python
yield {"type": "dashboard", "data": {"dashboard_id": dashboard.id}}
```

Нужно заменить на готовый `DashboardPayload` (он уже описан в
`api/schemas/chat.py:45-59`):

```python
from app.api.schemas.chat import DashboardPayload
...
payload = DashboardPayload.from_dashboard(dashboard)
yield {"type": "dashboard", "data": payload.model_dump()}
```

`from_dashboard` уже исключает `snapshot_data` из плиток — соответствует B-06.
Если правка не входит в зону ответственности фронта, зафиксировать её в issue
B-06 и считать F-06 заблокированной до мержа B-06. UI при этом пишется и
проверяется на `dev:mock` (в `mockData.ts:354-363` плитки уже есть).

### 4.2. `frontend/src/lib/tileLayout.ts`

Добавить раскладку компактного превью, не ломая существующую `tileGridLayout`:

```ts
export const PREVIEW_GAP = 8;

export interface PreviewItem {
  key: string;
  tile: DashboardTile;
  /** 1 — KPI в половину строки, 2 — график во всю ширину. */
  span: 1 | 2;
}

/** Порядок чтения: сверху вниз, слева направо; KPI может стоять в паре. */
export function previewGrid(tiles: DashboardTile[], isKpi: (t: DashboardTile) => boolean): PreviewItem[] {
  return [...tiles]
    .sort((a, b) => (a.layout?.y ?? 0) - (b.layout?.y ?? 0) || (a.layout?.x ?? 0) - (b.layout?.x ?? 0))
    .map((tile, i) => ({
      key: tile.id || `tile-${i}`,
      tile,
      span: isKpi(tile) ? 1 : 2,
    }));
}
```

Почему не `tileGridLayout`: в чате нет 12-колоночной сетки и drag&drop; нужен
простой CSS-grid с двумя колонками и предсказуемой высотой. `previewGrid`
сохраняет порядок из `layout`, поэтому превью совпадает с полной страницей.

### 4.3. `frontend/src/components/canvas/DashboardTile.tsx`

Добавить проп `preview?: boolean` (рядом с `mini`/`large`):

```ts
interface Props {
  // ...
  /** Компактная плитка в ленте чата: график/KPI + инсайт, без действий. */
  preview?: boolean;
}
```

В рендере:
- `preview` — самый компактный вариант: `p-2`, заголовок `text-[10px]`,
  KPI `text-xl`, инсайт `line-clamp-2 text-[10px]`, без кнопки «развернуть»
  и без inline-редактирования;
- график занимает `h-28`/`h-32` (задаёт родитель), внутри `FittedChart`;
- `stale`-бейдж оставить, но уменьшить;
- клик по плитке в превью не должен ничего открывать (`onOpen` не передаём);
- `mini` не менять: он используется в реестре `/dashboards`.

Рекомендуемая реализация — отдельная ветка рендера до `if (mini)`, чтобы не
усложнять основной вариант:

```tsx
if (preview) {
  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden rounded-lg border border-graphite-800 bg-graphite-900 p-2">
      <div className="mb-1 flex items-center gap-1.5">
        {isKpi && <span className="...">KPI</span>}
        <span className="truncate text-[10px] font-medium uppercase tracking-wide text-graphite-400">
          {tile.title}
        </span>
      </div>
      {isKpi && kpi ? (
        <div className="text-xl font-bold text-graphite-50">{kpi.value}</div>
      ) : hasChart ? (
        <div className="min-h-0 flex-1"><FittedChart tile={tile} colors={colors} /></div>
      ) : null}
      {tile.insight && (
        <p className="mt-1 line-clamp-2 text-[10px] leading-snug text-graphite-300">{tile.insight}</p>
      )}
    </div>
  );
}
```

### 4.4. `frontend/src/components/chat/DashboardPreviewCard.tsx` (новый)

```tsx
interface Props {
  dashboard: DashboardEvent["data"];
  onOpenDashboard: () => void;
  onOpenCanvas: () => void;
}
```

Структура:
1. **Шапка:** иконка `layout`, `dashboard.title`, счётчик плиток
   (`chat.dashboardTiles`, `{n}`), справа две кнопки:
   - «Открыть дашборд» (`chat.openDashboard`, иконка `maximize`);
   - «Открыть на канвасе» (`chat.toCanvas`, иконка `panelRight`,
     `title={t("chat.toCanvasTitle")}`).
2. **Сетка:**

```tsx
const items = previewGrid(dashboard.tiles ?? [], isKpiTile);
<div className="grid grid-cols-2 gap-2">
  {items.map(({ key, tile, span }) => (
    <div key={key} className={span === 2 ? "col-span-2 h-[190px]" : "col-span-1 h-[104px]"}>
      <DashboardTile tile={tile} preview />
    </div>
  ))}
</div>
```

3. **Пустые `tiles` (старый бэкенд):**
   - не вызывать `fetchDashboard`;
   - показать строку `chat.dashboardPreparing` и те же две кнопки;
   - при клике «Открыть дашборд» полная страница сама сделает `GET` — это
     допустимо, потому что это уже переход, а не рендер превью из события.
4. **Много плиток:** `max-h-[460px] overflow-y-auto pr-1`, чтобы сообщение не
   занимало весь экран; скролл внутри карточки.
5. **Мобильная ширина:** на `<640px` все плитки `col-span-2`; проверить, что
   `min-w-0` и `truncate` не дают горизонтальный скролл.

### 4.5. `frontend/src/components/chat/ChatPanel.tsx`

1. В обработчике `event.type === "dashboard"`:
   - по-прежнему `addMessage({ ..., dashboard: dashData })`;
   - `setCanvasDashboard(...)` вызывать только если `dashData.tiles?.length > 0`
     (чтобы не затирать текущий канвас пустым набором);
   - **не** вызывать `setCanvasOpen(true)` автоматически: превью уже в ленте,
     панель открывается кнопкой «Открыть на канвасе»;
   - `fetchDashboard` не импортировать и не вызывать в этой ветке.
2. В рендере сообщения:
   - для `m.dashboard` заменить текущую карточку-ссылку на
     `<DashboardPreviewCard ... />`;
   - для `m.chart` оставить существующую компактную карточку;
   - `handleOpenDashboard` и `handleLoadToCanvas` уже есть; типизировать
     аргумент как `DashboardEvent["data"]`.
3. Проверить, что `handleLoadToCanvas` не делает `GET`: он только заполняет
   `canvasStore` из события и открывает панель.
4. Если событие `dashboard` пришло без `tiles`, карточка всё равно должна
   появиться; это защищает от смешанных версий фронта/бэка.

### 4.6. i18n

| Ключ | RU | EN |
|---|---|---|
| `chat.dashboardTiles` | Плиток: {n} | Tiles: {n} |
| `chat.dashboardPreview` | Превью дашборда | Dashboard preview |
| `chat.dashboardPreparing` | Плитки появятся после обновления ответа агента | Tiles will appear after the agent response is updated |

`chat.openDashboard`, `chat.toCanvas`, `chat.toCanvasTitle`,
`chat.dashboardDefault` уже есть — переиспользовать.

### 4.7. Проверка F-06

1. `npm run dev:mock`, задать вопрос «Создай дашборд» → в ленте сразу
   появляется сетка: KPI и график с инсайтами.
2. В DevTools → Network убедиться, что после события `dashboard` нет запроса
   `GET /api/dashboards/{id}` (событие приходит в SSE-потоке `POST /chat`).
3. «Открыть дашборд» → `/dashboard/{id}` и полная страница.
4. «Открыть на канвасе» → правая панель с теми же плитками.
5. С реальным backend: повторить после мержа B-06; при старом backend —
   карточка с кнопками без падения.
6. Узкое окно (<640px) — нет горизонтального скролла.

**Готово, когда:** дашборд из чата рендерится сразу, ссылка ведёт на полную
страницу, запросов за дашбордом после события нет.

---

## 5. F-08 · Канвас: drag&drop, resize, сохранение раскладки

**Цель:** раскладка переживает перезагрузку; на 1280px и 1920px плитки не
налезают друг на друга; действия плитки доступны из меню; тип графика можно
сменить без LLM.

### 5.1. `frontend/src/lib/tileLayout.ts`

1. Заменить константу минимальных размеров:

```ts
export const MIN_SIZE = {
  kpi: { w: 2, h: 2 },
  chart: { w: 4, h: 3 },
} as const;
```

2. `tileGridLayout`:
   - `minW`/`minH` брать из `MIN_SIZE`;
   - clamp'ить и `w`, и `h` (сейчас clamp'ится только `h`);
   - не менять сигнатуру и поведение `stack` — она нужна для узкой панели;
   - не позволять `x + w > 12`: `w = Math.min(w, 12 - x)`.

3. Добавить преобразования API ↔ RGL, чтобы не дублировать код в двух страницах:

```ts
export function layoutFromTile(tile: DashboardTile, index: number): Layout {
  // fallback: KPI 6×3, график 12×5; затем clamp по MIN_SIZE и cols=12
}

export function applyGridLayout(tiles: DashboardTile[], layout: Layout[]): DashboardTile[] {
  const byKey = new Map(layout.map((item) => [item.i, item]));
  return tiles.map((tile, i) => {
    const item = byKey.get(tile.id || `tile-${i}`);
    return item
      ? { ...tile, layout: { x: item.x, y: item.y, w: item.w, h: item.h } }
      : tile;
  });
}
```

`tileGridLayout` строить поверх `layoutFromTile`, чтобы страница дашборда и
канвас использовали одни и те же минимальные размеры.

### 5.2. `frontend/src/components/canvas/FitGrid.tsx`

Требование — `ResponsiveGridLayout` с 12 колонками и `rowHeight = 60`.
`FitGrid` уже измеряет ширину контейнера через `ResizeObserver`; это важно,
потому что канвас живёт в перетаскиваемой панели и `WidthProvider` по окну
даст неверную ширину.

План:
1. Добавить в динамический импорт `Responsive`:

```tsx
const ResponsiveGridLayout = dynamic(
  () =>
    import("react-grid-layout").then((mod) => {
      const lib = ((mod as any).default ?? mod) as typeof mod;
      return lib.Responsive as any;
    }),
  { ssr: false },
);
```

2. Расширить пропсы:

```ts
type Props = Omit<ResponsiveProps, "width" | "layouts"> & {
  zoom: number;
  layout: Layout[];
  responsive?: boolean;
  children: React.ReactNode;
};
```

3. При `responsive` рендерить:

```tsx
<ResponsiveGridLayout
  {...props}
  width={gridWidth}
  breakpoints={{ lg: 0 }}
  cols={{ lg: 12 }}
  layouts={{ lg: layout }}
  rowHeight={60}
  onLayoutChange={(current: Layout[]) => onLayoutChange?.(current)}
/>
```

Один breakpoint `lg: 0` + `cols: 12` — это всегда 12 колонок, как в макете;
`Responsive` при этом получает ширину из `useElementSize`, а не из окна.
`rowHeight=60` — базовый, зум применяется снаружи (`rowHeight={Math.round(60 * zoom)}`).

4. Сохранить обратную совместимость: остальные страницы (`/dashboard/[id]`)
   могут продолжать использовать обычный `GridLayout`, пока их не переведём.
   После F-08 перевести и их на `responsive`.

### 5.3. `frontend/src/components/canvas/Canvas.tsx`

1. **Единый источник раскладки.**
   - `gridLayout = tileGridLayout(displayTiles, isKpiTile, { stack })`;
   - `onLayoutChange` → `handleLayoutChange(current)`:
     ```ts
     const next = applyGridLayout(displayTiles, current);
     displayTiles.forEach((tile, i) => {
       const before = tile.layout;
       const after = next[i].layout;
       if (before.x !== after.x || before.y !== after.y || before.w !== after.w || before.h !== after.h) {
         updateTileLayout(i, after);
       }
     });
     ```
   - `updateTileLayout` вызывать только для изменившихся плиток;
   - если `dashboardId === null`, писать в `sessionStore.setTiles(next)`,
     иначе — только в `canvasStore`;
   - **игнорировать** колбэк при `stack === true` и при первом монтировании
     (сравнение layout'ов это уже отсекает, но добавить `hasMountedRef` для
     явности).

2. **Debounce-сохранение 800 мс.**
   - в `canvasStore` добавить `layoutRevision` (см. §5.4), увеличивать его в
     `updateTileLayout`;
   - в `Canvas`:
     ```ts
     useEffect(() => {
       if (!dashboardId || layoutRevision === 0 || stack) return;
       const timer = setTimeout(async () => {
         const snapshot = useCanvasStore.getState().tiles;
         if (!snapshot.length) return;
         try {
           setSaveState("saving");
           await updateDashboard(dashboardId, { tiles: snapshot });
           setSaveState("saved");
         } catch (e) {
           setSaveState("error", e);
         }
       }, 800);
       return () => clearTimeout(timer);
     }, [layoutRevision, dashboardId, stack]);
     ```
   - прочитать `tiles` внутри таймера из `getState()`, чтобы не сохранять
     устаревший снимок;
   - защита от гонок: `saveSeqRef` — увеличивать перед запросом и игнорировать
     ответ, если пришёл более новый; при размонтировании очищать таймер;
   - после успешного auto-save **не** вызывать `setTiles(response.tiles)`:
     backend сохраняет те же ID и layout, а замена вызовет лишний
     `onLayoutChange` и повторное сохранение;
   - не auto-save'ить, если `dashboardId === null`: черновик сохраняется
     кнопкой «Сохранить» (она делает `POST` и получает ID).

3. **Индикатор «Сохранено / Сохраняю / Ошибка».**
   - в шапке рядом с кнопкой сохранения:
     - `saving` — `Icon name="refresh" className="animate-spin"` + `canvas.saveStateSaving`;
     - `saved` — `Icon name="check"` + `canvas.saveStateSaved` (через 2 с → `idle`);
     - `error` — `Icon name="alertTriangle"` + `canvas.saveStateError` с tooltip
       текста ошибки и кнопкой повтора (вызвать `updateDashboard` вручную).
   - ручное сохранение (`handleSave`) оставить для создания дашборда и
     принудительного сохранения; оно тоже выставляет `saveState`.

4. **Параметры сетки.**
   - `cols={12}`;
   - `rowHeight={Math.round(60 * zoom)}` (база 60);
   - `margin={[14, 14]}`, `containerPadding={[0, 0]}` оставить;
   - `isDraggable={isEditing && !stack}`, `isResizable={isEditing && !stack}`;
   - `onLayoutChange` — единственный обработчик сохранения (RGL вызывает его
     после drag/resize stop).

### 5.4. `frontend/src/store/canvasStore.ts`

Добавить состояние автосохранения:

```ts
export type SaveState = "idle" | "saving" | "saved" | "error";

interface CanvasState {
  // ...
  layoutRevision: number;
  saveState: SaveState;
  saveError: string | null;

  updateTileLayout: (index: number, layout: DashboardTileLayout) => void;
  updateTileLayouts: (layouts: Record<number, DashboardTileLayout>) => void;
  setSaveState: (state: SaveState, error?: unknown) => void;
}
```

Поведение:
- `layoutRevision: 0`, `saveState: "idle"`, `saveError: null`;
- `updateTileLayout` обновляет плитку **и** `layoutRevision += 1`;
- `updateTileLayouts` — батч-версия для `onLayoutChange`, чтобы не делать
  N подряд идущих `set` (полезно при перетаскивании нескольких плиток);
- `setSaveState("error", err)` сохраняет `err.detail || err.message`;
- `setDashboard(...)` сбрасывает `layoutRevision: 0`, `saveState: "idle"`,
  `saveError: null` — загрузка нового дашборда не должна запускать автосохранение;
- `clearCanvas()` сбрасывает новые поля;
- `setTiles()` не увеличивает `layoutRevision` — это загрузка/пересчёт, а не
  действие пользователя.

### 5.5. `ItemActionMenu` и действия плитки

#### 5.5.1. `frontend/src/components/layout/ItemActionMenu.tsx`

1. Расширить тип:

```ts
export type ActionMenuTarget = "session" | "csv" | "db" | "tile";
```

2. Сделать `onTogglePin` необязательным (для плитки pin не нужен) и добавить:

```ts
chartType?: string;
chartTypes?: readonly string[];
onChartTypeChange?: (type: string) => void;
onOpen?: () => void;
```

3. В `view` добавить `"chart_type"`; при `target === "tile"`:
   - скрыть pin;
   - пункты: «Переименовать», «Сменить тип графика», «Удалить»;
   - `chart_type` — список radio/кнопок из `chartTypes`;
   - текущий тип подсвечивать, выбор вызывает `onChartTypeChange` и закрывает
     меню (либо оставляет открытым для сравнения — решить на UI-ревью).
4. Для `session/csv/db` поведение и тексты не менять — там меню используется
   в сайдбаре.

#### 5.5.2. `frontend/src/components/canvas/DashboardTile.tsx`

- Добавить `onMenu?: (anchorRect: DOMRect) => void`;
- в режиме `isEditing` показывать кнопку `moreHorizontal` в шапке;
- кнопка вызывает `onMenu(e.currentTarget.getBoundingClientRect())`;
- inline-кнопку удаления оставить как быстрый доступ, но меню — основной путь;
- для `preview`/`mini` меню не показывать.

#### 5.5.3. `frontend/src/components/canvas/Canvas.tsx`

1. Состояние меню:
   ```ts
   const [menu, setMenu] = useState<{ index: number; rect: DOMRect } | null>(null);
   ```
2. В `DashboardTile` передать `onMenu={(rect) => setMenu({ index: i, rect })}`.
3. В корне Canvas (вне скролла, `position: fixed` внутри `ItemActionMenu`)
   рендерить:
   ```tsx
   {menu && (
     <ItemActionMenu
       target="tile"
       title={displayTiles[menu.index]?.title ?? ""}
       anchorRect={menu.rect}
       chartType={currentChartType(displayTiles[menu.index])}
       chartTypes={CHART_TYPES}
       onClose={() => setMenu(null)}
       onRename={(title) => updateTile(menu.index, { title })}
       onDelete={() => removeTile(menu.index)}
       onChartTypeChange={(type) => handleChartTypeChange(menu.index, type)}
     />
   )}
   ```
   `onTogglePin` не передавать.

#### 5.5.4. Смена типа графика без LLM

Добавить `frontend/src/lib/chartTypes.ts`:

```ts
export const CHART_TYPES = [
  "bar", "line", "area", "scatter", "histogram", "box", "heatmap", "table", "kpi",
] as const;

export type ChartType = (typeof CHART_TYPES)[number];

export const CHART_MARK: Record<ChartType, unknown> = {
  bar: "bar",
  line: "line",
  area: "area",
  scatter: "point",
  histogram: "bar",
  box: "boxplot",
  heatmap: "rect",
  table: "text",
  kpi: { type: "text", fontSize: 48 },
};
```

Алгоритм `handleChartTypeChange(index, type)`:
1. Взять `tile = displayTiles[index]`, `prevTiles = displayTiles`.
2. Собрать `spec`:
   ```ts
   const usermeta = { ...(tile.spec?.usermeta as object), chart_type: type, requested_type: type };
   const spec = { ...tile.spec, usermeta, mark: CHART_MARK[type] };
   ```
   `requested_type` обязателен: `recalculate.rebuild_spec` читает его первым.
3. `is_kpi = type === "kpi"`; layout не меньше `MIN_SIZE.kpi` для KPI и
   `MIN_SIZE.chart` для остальных.
4. Оптимистично `setTiles(nextTiles)`.
5. Если `dashboardId` есть:
   ```ts
   await updateDashboard(dashboardId, { tiles: nextTiles });
   const updated = await recalculateDashboard(dashboardId);
   setTiles(updated.tiles);
   setSaveState("saved");
   ```
   Если `result_table` пуст — `recalculate` не перестроит спеку; всё равно
   сохраняем `spec` через `PUT`, а пользователю показываем
   `canvas.chartTypeLocalOnly`.
6. Если `dashboardId === null` — только локальное изменение; тип уйдёт на
   сервер при создании дашборда.
7. При ошибке — вернуть `prevTiles`, `setSaveState("error", err)`.

Важно: `isKpiTile` определяет KPI по `is_kpi || spec.mark === "text"`;
поэтому при переключении **из** KPI нужно явно выставить `is_kpi: false` и
`mark` нового типа, иначе плитка останется KPI.

### 5.6. Плотный режим плитки и проверка 1280/1920

Минимальные KPI 2×2 и график 4×3 — это меньше, чем было (3×3 и 4×4), поэтому
нужно уплотнить содержимое:

1. `DashboardTile` принимает `dense?: boolean`; Canvas вычисляет его по
   `gridLayout[i].h`: `dense = h <= MIN_SIZE.chart.h`.
2. В `dense`:
   - `p-3` вместо `p-4`, `mb-1.5` вместо `mb-2`;
   - заголовок `text-[11px]`, KPI `text-2xl`, инсайт `line-clamp-1`;
   - `result_table` скрывать (он не критичен в плотной сетке).
3. KPI 2×2 = 2 × 60 = 120 px: заголовок + цифра + подпись помещаются при
   `p-3`; инсайт в этом режиме лучше скрыть полностью (только tooltip).
4. График 4×3 = 180 px: заголовок 24 px + график ~100 px + инсайт 40 px.
5. Проверка:
   - 1920×1080: правая панель шире, плитки не растягиваются за 12 колонок;
   - 1280×800: панель уже; при `<560px` включается `stack`, drag&drop
     отключается, плитки идут одной колонкой и не налезают;
   - 1280 при открытом сайдбаре и канвасе: проверить, что `FitGrid`
     пересчитывает ширину при перетаскивании сплиттера, а не только при
     `window.resize`;
   - 1920 с `zoom > 1`: появляется горизонтальный скролл у `FitGrid`, плитки
     не ломаются.
6. `DashboardTile` должен иметь `overflow-hidden` и `min-h-0` на всех
   flex-контейнерах, иначе инсайт вытолкнет график.

### 5.7. i18n для F-08

| Ключ | RU | EN |
|---|---|---|
| `canvas.saveStateSaving` | Сохраняю… | Saving… |
| `canvas.saveStateSaved` | Сохранено | Saved |
| `canvas.saveStateError` | Ошибка сохранения | Save failed |
| `canvas.saveStateRetry` | Повторить сохранение | Retry save |
| `canvas.chartType` | Сменить тип графика | Change chart type |
| `canvas.chartTypeLocalOnly` | Тип сохранён; данные пересчитаются после обновления | Type saved; data will refresh later |
| `canvas.tileMenu` | Действия плитки | Tile actions |
| `chartType.bar` | Столбцы | Bar |
| `chartType.line` | Линия | Line |
| `chartType.area` | Область | Area |
| `chartType.scatter` | Точки | Scatter |
| `chartType.histogram` | Гистограмма | Histogram |
| `chartType.box` | Ящик с усами | Box |
| `chartType.heatmap` | Тепловая карта | Heatmap |
| `chartType.table` | Таблица | Table |
| `chartType.kpi` | KPI | KPI |

### 5.8. Проверка F-08

```bash
cd frontend
npx tsc --noEmit
npm run lint
npm run build
```

Ручной сценарий:
1. Открыть `/dashboard/{id}`, нажать «Редактировать».
2. Перетащить KPI и график, изменить размеры → в шапке «Сохраняю…» →
   «Сохранено».
3. F5 → раскладка та же (значения `x/y/w/h` в `PUT /api/dashboards/{id}`).
4. Уменьшить KPI до 2×2 и график до 4×3 — RGL не даёт меньше, содержимое
   обрезается аккуратно, без наложения.
5. Открыть меню плитки → переименовать → F5: новое имя.
6. Меню → «Сменить тип графика»: `bar → line` → `PUT`, затем
   `POST /recalculate`; в ответе `spec.usermeta.chart_type === "line"` и
   `requested_type === "line"`; F5 сохраняет тип.
7. Меню → удалить → плитка исчезает, F5 — её нет.
8. Проверить 1280×800 и 1920×1080, а также перетаскивание сплиттера.
9. Отключить backend на 10 секунд → изменить раскладку → индикатор
   «Ошибка сохранения»; вернуть backend → «Повторить сохранение» → успех.

**Готово, когда:** раскладка переживает перезагрузку; на 1280px и 1920px
ничего не налезает; auto-save и индикатор работают; меню плитки позволяет
переименовать, удалить и сменить тип графика.

---

## 6. Порядок работ и коммиты

Ветка `feature/F-06-F-08-dashboard-ui` создана от `feature/F-01-frontend-ui`.
PR — обратно в `feature/F-01-frontend-ui` (не в `dev`), пока эпик #28 не
смержен; перед PR сделать `git rebase feature/F-01-frontend-ui`.

Рекомендуемые коммиты (по одному на задачу + правки по ревью):

| № | Коммит | Содержимое |
|---|---|---|
| 1 | `docs: план F-06–F-08` | этот файл |
| 2 | `feat(f-07): снапшот, 404 и последние дашборды в сайдбаре` | §3 |
| 3 | `feat(f-06): превью дашборда из SSE в чате` | §4 (+ backend B-06, если разрешено) |
| 4 | `feat(f-08): responsive-сетка и автосохранение раскладки` | §5.1–5.4 |
| 5 | `feat(f-08): меню плитки и смена типа графика` | §5.5–5.7 |

Порядок внутри F-08:
1. `tileLayout` + `FitGrid` (риск SSR/типов);
2. `canvasStore` + `Canvas` auto-save;
3. `ItemActionMenu` + смена типа;
4. плотный режим и проверка ширин.

---

## 7. Риски и принятые решения

| Риск / вопрос | Решение |
|---|---|
| B-06 ещё не отдаёт `tiles` в SSE | Добавить `DashboardPayload.from_dashboard` в orchestrator (3 строки) или ждать B-06; UI проверять на `dev:mock`, fallback без `GET` |
| `ResponsiveGridLayout` меряет окно, а не панель | Передавать `width` из `useElementSize` (`FitGrid`), один breakpoint `lg: 0`, `cols: 12` |
| `onLayoutChange` вызывается на mount и зацикливает сохранение | Сравнивать layout до/после, `layoutRevision` только при реальном изменении, не заменять `tiles` ответом auto-save |
| Гонка двух auto-save | `saveSeqRef` + очистка таймера; последний запрос выигрывает |
| `PUT` может перевыдать ID плиток | После `PUT`/`recalculate` брать `tiles` из ответа; ID менять только если backend вернул новые |
| `requested_type` перебивает `chart_type` | При смене типа выставлять оба поля |
| `isKpiTile` остаётся true после ухода с KPI | Явно `is_kpi: false` + новый `mark` |
| `snapshot_at` есть только у плиток | `dashboardSnapshotAt` = max по плиткам, fallback `updated_at` → `created_at` |
| N+1 запросов превью в реестре | `Promise.allSettled` + ограничение параллелизма; не блокировать список |
| Сайдбар делает лишние `GET` | Кэш на компонент, `AbortController`, обновление по `pathname` / `canvasDashboardId` / событию `dashboards:changed` |
| Узкая панель и drag&drop | При `stack` drag/resize выключены, раскладка не сохраняется; при возврате ширины — прежняя сохранённая |
| Минимальные 2×2 ломают вёрстку KPI | `dense`-режим: меньше padding, инсайт скрыт/в одну строку |
| F-08 зависит от F-07 | Сначала мержим F-07; auto-save включаем только при `dashboardId !== null` |

---

## 8. Общий чек-лист готовности эпика (F-06–F-08)

- [ ] F-07: дашборд из чата открывается после F5 и рестарта бэкенда.
- [ ] F-07: 404 → «не найден», сеть/5xx → ошибка с повтором; DELETE 204.
- [ ] F-07: «Снимок от …» по `snapshot_at`; `stale`-плитки помечены.
- [ ] F-07: `/dashboards` — список, число плиток, удаление с подтверждением.
- [ ] F-07: в сайдбаре ссылка `/dashboards` и последние 5 дашбордов.
- [ ] F-06: событие `dashboard` рендерит компактную сетку из `data.tiles`.
- [ ] F-06: «Открыть дашборд» ведёт на `/dashboard/[id]`.
- [ ] F-06: «Открыть на канвасе» заполняет правую панель.
- [ ] F-06: после события нет `GET /api/dashboards/{id}`.
- [ ] F-08: `ResponsiveGridLayout`, 12 колонок, `rowHeight` 60.
- [ ] F-08: `onLayoutChange` → `updateTileLayout`; debounce 800 мс → `PUT`.
- [ ] F-08: индикатор «Сохранено / Сохраняю / Ошибка».
- [ ] F-08: меню плитки — переименовать, удалить, сменить тип графика.
- [ ] F-08: KPI минимум 2×2, график 4×3.
- [ ] F-08: раскладка переживает F5; 1280px и 1920px без наложений.
- [ ] `npx tsc --noEmit`, `npm run lint`, `npm run build` — зелёные.
- [ ] Backend-тесты дашбордов: `pytest backend/tests/unit/test_dashboards_api.py`.

---

## 9. Единая команда проверки

```bash
# фронт
cd frontend
npm ci
npx tsc --noEmit
npm run lint
npm run build

# бэкенд (контракт B-05/B-06)
cd ../backend
pytest tests/unit/test_dashboards_api.py tests/contract/test_chat_event_schema.py -q

# ручной E2E
cd ../frontend
npm run dev:mock      # UI без бэкенда
# или
npm run dev           # реальный API
```

Если `tests/contract/test_chat_event_schema.py` ещё не содержит проверки
`DashboardPayload`, добавить её вместе с backend-правкой B-06: событие
`dashboard` должно валидироваться по `DashboardPayload`, а `tiles` — содержать
`layout`, `spec`, `is_kpi`, `snapshot_at` и не содержать `snapshot_data`.
