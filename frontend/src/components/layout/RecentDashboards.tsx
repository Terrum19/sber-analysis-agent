"use client";

/**
 * Последние 5 дашбордов в левом сайдбаре. Обновляется при смене страницы,
 * при создании/загрузке дашборда в канвасе и по событию `dashboards:changed`
 * (его шлёт страница реестра после удаления).
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { fetchDashboards } from "@/api/client";
import { useCanvasStore } from "@/store/canvasStore";
import { useT } from "@/i18n/useT";
import Icon from "@/components/ui/Icon";
import type { DashboardListItem } from "@/types/dashboard";

const MAX_ITEMS = 5;

function updatedAt(item: DashboardListItem): number {
  const value = item.updated_at ?? item.created_at;
  if (typeof value === "number") return value;
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? 0 : parsed;
  }
  return 0;
}

export default function RecentDashboards() {
  const t = useT();
  const pathname = usePathname();
  const canvasDashboardId = useCanvasStore((s) => s.dashboardId);
  const [items, setItems] = useState<DashboardListItem[]>([]);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const handleChanged = () => setRevision((value) => value + 1);
    window.addEventListener("dashboards:changed", handleChanged);
    return () => window.removeEventListener("dashboards:changed", handleChanged);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchDashboards(undefined, controller.signal)
      .then((list) => {
        if (controller.signal.aborted) return;
        setItems(
          [...list]
            .sort((a, b) => updatedAt(b) - updatedAt(a))
            .slice(0, MAX_ITEMS),
        );
      })
      .catch(() => undefined); // сайдбар не должен ломать приложение
    return () => controller.abort();
  }, [pathname, canvasDashboardId, revision]);

  if (items.length === 0) return null;

  return (
    <div className="mt-1">
      <span className="block px-2.5 pb-1 text-[11px] font-medium text-graphite-500">
        {t("sidebar.recentDashboards")}
      </span>
      <div className="space-y-0.5">
        {items.map((item) => {
          const href = `/dashboard/${item.id}`;
          const active = pathname === href;
          return (
            <Link
              key={item.id}
              href={href}
              title={item.title}
              className={`flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[12px] transition ${
                active
                  ? "bg-graphite-800 text-graphite-100"
                  : "text-graphite-400 hover:bg-graphite-900 hover:text-graphite-100"
              }`}
            >
              <Icon name="layout" size={13} className="shrink-0 text-graphite-500" />
              <span className="truncate">{item.title}</span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
