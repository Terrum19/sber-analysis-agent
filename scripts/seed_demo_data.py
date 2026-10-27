"""Накачка демо-данных в DuckDB для сценариев №1-2.

Создаёт маленький синтетический CSV (пользователи + события по месяцам),
чтобы платформу можно было проверить без реального датасета.

Запуск: python scripts/seed_demo_data.py
"""

import csv
from pathlib import Path


def seed(out_dir: Path = Path("data/sample_datasets")) -> None:
    """Сгенерировать demo_users.csv и demo_events.csv."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "demo_users.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "region", "signup_date"])
        for i in range(100):
            w.writerow(
                [
                    f"u{i:03d}",
                    ["Москва", "СПб", "Казань"][i % 3],
                    f"2024-{(i % 12) + 1:02d}-01",
                ]
            )
    with (out_dir / "demo_events.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["event_id", "user_id", "amount", "event_date"])
        for i in range(500):
            w.writerow(
                [
                    f"e{i:04d}",
                    f"u{i % 100:03d}",
                    round(100 + (i * 37) % 900, 2),
                    f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                ]
            )


if __name__ == "__main__":
    seed()
    print("Демо-данные созданы в data/sample_datasets/")
