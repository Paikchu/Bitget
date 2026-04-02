.PHONY: sandbox build dev

sandbox:
	docker build -f sandbox/Dockerfile.sandbox -t strategy-sandbox:latest sandbox/

build: sandbox
	docker build -t bitget-bot:latest .

dev: sandbox
	uvicorn bitget_bot.api:app --host 0.0.0.0 --port 8080 --reload
