# NetworkMapper Makefile

.PHONY: help build up down logs clean install-backend install-frontend dev-backend dev-frontend dev setup status alembic-roundtrip check check-fast

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Build all Docker images
	docker compose build

up: ## Start all services
	docker compose up -d

down: ## Stop all services
	docker compose down

logs: ## Show logs from all services
	docker compose logs -f

clean: ## DESTROYS this project's containers AND its database volume (no backup; use scripts/deploy.sh option 4 for one)
	docker compose down -v --remove-orphans

install-backend: ## Install backend dependencies (runtime + test/lint tools, pinned by constraints.txt)
	cd backend && pip install -r requirements-dev.txt -c constraints.txt

install-frontend: ## Install frontend dependencies
	cd frontend && npm install

dev-backend: ## Run backend in development mode
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## Run frontend in development mode
	cd frontend && npm start

dev: ## Run both backend and frontend in development mode
	@echo "Starting backend and frontend in development mode..."
	@echo "Backend (uvicorn, plain HTTP, no nginx): http://localhost:8000"
	@echo "Frontend (Vite dev server):              http://localhost:3000"
	@make -j2 dev-backend dev-frontend

setup: ## Initial setup - install dependencies and start services
	@echo "Setting up NetworkMapper..."
	@make build
	@make up
	@echo "NetworkMapper is now running!"
	@echo "Frontend:          https://localhost"
	@echo "Backend API:       https://localhost/api/v1   (proxied by nginx; the backend's :8000 is not published)"
	@echo "API Documentation: https://localhost/docs"

status: ## Show status of all services
	docker compose ps

alembic-roundtrip: ## Verify every Alembic downgrade() inverts cleanly (boots throwaway Postgres)
	./scripts/test-alembic-roundtrip.sh

check: ## The gate: backend suite (report-worker image, Quarto tests must run), ruff, frontend tsc + eslint + vitest, Alembic round trip
	./scripts/check.sh

check-fast: ## The gate without the Alembic round trip
	./scripts/check.sh --fast
