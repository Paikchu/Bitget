.PHONY: sandbox build dev \
	docker-build docker-up docker-down docker-logs docker-restart docker-ps \
	browser-install browser-check

DOCKER_BUILDKIT ?= 0
COMPOSE_DOCKER_CLI_BUILD ?= 0
COMPOSE_CMD = DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) COMPOSE_DOCKER_CLI_BUILD=$(COMPOSE_DOCKER_CLI_BUILD) docker compose
DOCKER_BUILD_CMD = DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker build
PLAYWRIGHT_BROWSERS_PATH ?= $(CURDIR)/.playwright-browsers
BROWSER_URL ?= http://127.0.0.1:8080
BROWSER_EXPECT_TEXT ?= 实验回测
BROWSER_EXPECT_TITLE ?= Bitget Bot Dashboard
BROWSER_SCREENSHOT ?= output/browser-check.png

sandbox:
	$(DOCKER_BUILD_CMD) -f sandbox/Dockerfile.sandbox -t strategy-sandbox:latest sandbox/

build: sandbox
	$(DOCKER_BUILD_CMD) -t bitget-bot:latest .

dev: sandbox
	uvicorn bitget_bot.api:app --host 0.0.0.0 --port 8080 --reload

# --- Docker Compose（前端在镜像构建时 npm run build，改 UI 后须用 docker-up 而非仅 restart）---
# 需要：项目根目录 .env、Docker Desktop 已启动
# 启动后访问 http://127.0.0.1:8080

docker-build:
	$(COMPOSE_CMD) build

docker-up:
	$(COMPOSE_CMD) up -d --build

docker-down:
	$(COMPOSE_CMD) down

docker-logs:
	$(COMPOSE_CMD) logs -f bitget-bot

docker-restart:
	$(COMPOSE_CMD) restart bitget-bot

docker-ps:
	$(COMPOSE_CMD) ps

browser-install:
	cd frontend && npm install
	PLAYWRIGHT_BROWSERS_PATH="$(PLAYWRIGHT_BROWSERS_PATH)" ./frontend/node_modules/.bin/playwright install chromium

browser-check:
	NODE_PATH="$(CURDIR)/frontend/node_modules" PLAYWRIGHT_BROWSERS_PATH="$(PLAYWRIGHT_BROWSERS_PATH)" node scripts/browser_smoke_check.cjs --url "$(BROWSER_URL)" --expect-text "$(BROWSER_EXPECT_TEXT)" --expect-title "$(BROWSER_EXPECT_TITLE)" --screenshot "$(BROWSER_SCREENSHOT)"
