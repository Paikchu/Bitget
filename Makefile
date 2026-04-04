.PHONY: sandbox build dev \
	docker-up docker-down docker-logs docker-restart docker-ps

sandbox:
	docker build -f sandbox/Dockerfile.sandbox -t strategy-sandbox:latest sandbox/

build: sandbox
	docker build -t bitget-bot:latest .

dev: sandbox
	uvicorn bitget_bot.api:app --host 0.0.0.0 --port 8080 --reload

# --- Docker Compose（前端在镜像构建时 npm run build，改 UI 后须用 docker-up 而非仅 restart）---
# 需要：项目根目录 .env、Docker Desktop 已启动
# 启动后访问 http://127.0.0.1:8080

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f bitget-bot

docker-restart:
	docker compose restart bitget-bot

docker-ps:
	docker compose ps
