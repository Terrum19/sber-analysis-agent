from pydantic import BaseModel


class TemplateTile(BaseModel):
    title: str
    purpose: str
    chart_type: str
    is_kpi: bool = False
    insight_needed: bool = False


class DashboardTemplate(BaseModel):
    id: str
    name: str
    required_roles: dict[str, list[str]]
    tiles: list[TemplateTile]


TEMPLATES: tuple[DashboardTemplate, ...] = (
    DashboardTemplate(
        id="sales_quarterly",
        name="Продажи по кварталам",
        required_roles={
            "date": ["Дата операции (МСК)", "Дата операции", "order_date", "sale_date", "date"],
            "order": ["Код заказа", "order_id", "Заказ"],
            "product": ["Товар", "Артикул", "product", "product_name", "sku"],
            "quantity": ["Количество, шт.", "Количество", "quantity", "qty"],
            "price": ["Цена до скидки за единицу, руб.", "Цена", "price", "unit_price"],
        },
        tiles=[
            TemplateTile(
                title="Выручка по кварталам",
                purpose=(
                    "Выручка по кварталам даты операции: сумма количество × цена с учётом "
                    "скидки, возвраты вычитаются"
                ),
                chart_type="bar",
                insight_needed=True,
            ),
            TemplateTile(
                title="Топ товаров по выручке",
                purpose="10 товаров с наибольшей выручкой (количество × цена с учётом скидки)",
                chart_type="bar",
            ),
            TemplateTile(
                title="Выручка",
                purpose="Общая выручка за период: сумма количество × цена с учётом скидки",
                chart_type="kpi",
                is_kpi=True,
            ),
            TemplateTile(
                title="Средний чек",
                purpose="Средний чек: выручка, делённая на число различных заказов",
                chart_type="kpi",
                is_kpi=True,
            ),
        ],
    ),
    DashboardTemplate(
        id="app_events",
        name="События приложения",
        required_roles={
            "time": ["Время события (МСК)", "Время события", "event_time", "timestamp"],
            "user": ["Код пользователя", "user_id"],
            "session": ["Код сессии", "session_id"],
            "event": ["Событие", "event", "event_name"],
        },
        tiles=[
            TemplateTile(
                title="DAU",
                purpose="Число уникальных пользователей по дням времени события",
                chart_type="line",
                insight_needed=True,
            ),
            TemplateTile(
                title="Воронка покупки",
                purpose=(
                    "Пользователи на шагах воронки по событиям: начало сессии, просмотр товара, "
                    "добавление в корзину, начало оформления, заказ оплачен"
                ),
                chart_type="bar",
            ),
            TemplateTile(
                title="Удержание по неделям",
                purpose=(
                    "Доля пользователей, вернувшихся через N недель после недели первого события "
                    "(когорты по неделе первого события)"
                ),
                chart_type="heatmap",
            ),
        ],
    ),
    DashboardTemplate(
        id="support_tickets",
        name="Техподдержка",
        required_roles={
            "ticket": ["Код обращения", "ticket_id", "Обращение"],
            "created": ["Создано (МСК)", "Создано", "created_at"],
            "closed": ["Закрыто (МСК)", "Закрыто", "closed_at", "resolved_at"],
            "status": ["Статус", "status"],
        },
        tiles=[
            TemplateTile(
                title="Обращения по статусам",
                purpose="Число обращений в каждом статусе",
                chart_type="bar",
                insight_needed=True,
            ),
            TemplateTile(
                title="Обращения по неделям",
                purpose="Число созданных обращений по неделям даты создания",
                chart_type="line",
            ),
            TemplateTile(
                title="Время решения",
                purpose="Распределение часов от создания до закрытия по закрытым обращениям",
                chart_type="histogram",
            ),
            TemplateTile(
                title="Медианное время решения, ч",
                purpose="Медиана часов от создания до закрытия по закрытым обращениям",
                chart_type="kpi",
                is_kpi=True,
            ),
        ],
    ),
    DashboardTemplate(
        id="delivery",
        name="Доставка",
        required_roles={
            "shipment": ["Код отправления", "shipment_id", "Отправление"],
            "accepted": ["Принято (МСК)", "Принято", "accepted_at", "shipped_at"],
            "promised": ["Обещанный срок (МСК)", "Обещанный срок", "promised_at", "due_at"],
            "delivered": ["Доставлено (МСК)", "Доставлено", "delivered_at"],
            "carrier": ["Перевозчик", "carrier"],
        },
        tiles=[
            TemplateTile(
                title="Сроки доставки",
                purpose="Распределение дней от приёма до вручения по доставленным отправлениям",
                chart_type="histogram",
                insight_needed=True,
            ),
            TemplateTile(
                title="Доля опозданий",
                purpose="Доля доставленных отправлений, врученных позже обещанного срока",
                chart_type="kpi",
                is_kpi=True,
            ),
            TemplateTile(
                title="Опоздания по перевозчикам",
                purpose="Доля опозданий (вручено позже обещанного срока) по каждому перевозчику",
                chart_type="bar",
            ),
            TemplateTile(
                title="Отправления по неделям",
                purpose="Число принятых отправлений по неделям даты приёма",
                chart_type="line",
            ),
        ],
    ),
)


def normalize(name: str) -> str:
    return name.lstrip("﻿").strip().casefold()


def role_columns(
    template: DashboardTemplate,
    columns: list[str],
    titles: dict[str, str] | None = None,
) -> dict[str, str] | None:
    names = {
        column: {normalize(column), normalize((titles or {}).get(column) or column)}
        for column in columns
    }
    found = {}
    for role, variants in template.required_roles.items():
        wanted = {normalize(variant) for variant in variants}
        column = next((column for column in columns if names[column] & wanted), None)
        if column is None:
            return None
        found[role] = column
    return found


def match(columns: list[str], titles: dict[str, str] | None = None) -> DashboardTemplate | None:
    best = None
    for template in TEMPLATES:
        if role_columns(template, columns, titles) is None:
            continue
        if best is None or len(template.required_roles) > len(best.required_roles):
            best = template
    return best
