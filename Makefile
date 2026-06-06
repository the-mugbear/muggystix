# NetworkMapper Makefile

.PHONY: help build up down logs clean install-backend install-frontend dev-backend dev-frontend alembic-roundtrip

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Build all Docker images
	docker-compose build

up: ## Start all services
	docker-compose up -d

down: ## Stop all services
	docker-compose down

logs: ## Show logs from all services
	docker-compose logs -f

clean: ## Clean up Docker resources
	docker-compose down -v
	docker system prune -f

install-backend: ## Install backend dependencies
	cd backend && pip install -r requirements.txt

install-frontend: ## Install frontend dependencies
	cd frontend && npm install

dev-backend: ## Run backend in development mode
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## Run frontend in development mode
	cd frontend && npm start

dev: ## Run both backend and frontend in development mode
	@echo "Starting backend and frontend in development mode..."
	@echo "Backend will be available at https://localhost:8000"
	@echo "Frontend will be available at https://localhost:3000"
	@make -j2 dev-backend dev-frontend

setup: ## Initial setup - install dependencies and start services
	@echo "Setting up NetworkMapper..."
	@make build
	@make up
	@echo "NetworkMapper is now running!"
	@echo "Frontend: https://localhost:3000"
	@echo "Backend API: https://localhost:8000"
	@echo "API Documentation: https://localhost:8000/docs"

status: ## Show status of all services
	docker-compose ps

alembic-roundtrip: ## Verify every Alembic downgrade() inverts cleanly (boots throwaway Postgres)
	./scripts/test-alembic-roundtrip.sh