.PHONY: help install dev up down test lint model seed hk-data

HK_REPO = https://gitverse.ru/kban/HK_Datasets

help:
	@echo "make hooks    — git-хуки репозитория (сообщения коммитов, ruff)"
	@echo "make install  — зависимости backend+frontend"
	@echo "make seed     — демо-данные в DuckDB"
	@echo "make hk-data  — датасеты хакатона в DuckDB"
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

hk-data:
	@[ $$(ls data/hk_datasets/*.csv 2>/dev/null | wc -l) -eq 9 ] && [ -f data/hk_datasets/description.md ] || { tmp=$$(mktemp -d) && git clone --depth 1 $(HK_REPO) $$tmp && cp -n $$tmp/*.csv data/hk_datasets/ && cp -n $$tmp/README.md data/hk_datasets/description.md; rm -rf $$tmp; }
	python scripts/load_hk_datasets.py --no-llm

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
