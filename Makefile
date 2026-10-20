.PHONY: help hooks install dev up down test lint model seed

help:
	@echo "make hooks    — git-хуки репозитория (проверка сообщений коммитов)"
	@echo "make install  — зависимости backend+frontend"
	@echo "make seed     — демо-данные в DuckDB"
	@echo "make model    — подсказки по запуску LLM"
	@echo "make dev      — backend + frontend локально"
	@echo "make up/down  — docker compose"
	@echo "make test     — pytest"
	@echo "make lint     — ruff + next lint"

hooks:
	git config core.hooksPath .githooks

install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

seed:
	python scripts/seed_demo_data.py

model:
	bash scripts/download_model.sh

dev:
	bash infra/local-dev.sh

up:
	docker compose up --build

down:
	docker compose down

test:
	cd backend && pytest -q

lint:
	cd backend && ruff check app tests
	cd frontend && npm run lint
