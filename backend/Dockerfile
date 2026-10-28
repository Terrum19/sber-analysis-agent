FROM node:20-slim AS node

FROM python:3.11-slim

# Node.js 20 для WASM-песочницы (Pyodide); в Debian bookworm только 18-я версия
# без модели разрешений, поэтому бинарник берётся из официального образа.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

WORKDIR /app

# Шрифты для кириллического PDF-экспорта (ReportLab)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

# Зависимости и расширения DuckDB скачиваются на сборке, чтобы в рантайме
# контейнеру не нужна сеть и рестарт не тратил время на INSTALL.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import duckdb; con = duckdb.connect(); con.execute('INSTALL postgres_scanner'); con.execute('INSTALL clickhouse_scanner FROM community')"

# Pyodide и колёса numpy/pandas/scipy/scikit-learn/statsmodels — тоже на сборке:
# в рантайме песочница сети не имеет.
COPY sandbox/package.json sandbox/package-lock.json sandbox/fetch_packages.py ./sandbox/
RUN cd sandbox && npm ci --no-audit --no-fund && python fetch_packages.py
COPY sandbox/runner.mjs ./sandbox/

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
