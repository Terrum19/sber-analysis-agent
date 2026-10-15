FROM python:3.11-slim

WORKDIR /app

# Зависимости и расширения DuckDB скачиваются на сборке, чтобы в рантайме
# контейнеру не нужна сеть и рестарт не тратил время на INSTALL.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL postgres_scanner')"

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
