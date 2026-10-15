# Датасеты хакатона (HK_Datasets)

Девять CSV кейса «AI-Native BI Система»: продажи, техподдержка, задачи
команд, доставка, события приложения, рекламные кампании, заявки на
закупку, фоновые задания, лекарственные препараты. Описание колонок — в
README репозитория с данными.

Взять отсюда и положить файлы в эту папку:

```bash
git clone https://gitverse.ru/kban/HK_Datasets /tmp/HK_Datasets
cp /tmp/HK_Datasets/*.csv data/hk_datasets/
```

Формат: UTF-8, разделитель `;`, десятичная запятая. В DuckDB:

```sql
SELECT * FROM read_csv('data/hk_datasets/09_Лекарственные_препараты.csv',
                       delim = ';', decimal_separator = ',', header = true);
```

Вместе файлы занимают около 1,5 ГБ, отдельные — больше 200 МБ (лимит
GitHub — 100 МБ), поэтому всё содержимое папки, кроме этого README,
в `.gitignore` и в git не попадает.
