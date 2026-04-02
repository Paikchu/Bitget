# Phase 4 — Docker 部署到服务器

> 目标：将机器人 + API + 前端打包成一个 Docker 容器，实现一键部署，数据持久化，自动重启。

---

## 1. 新增文件总览

```
Bitget/
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
└── data/               # 运行时自动创建（SQLite 数据库存放处）
```

---

## 2. `Dockerfile`

采用多阶段构建：先构建前端，再打包进 Python 镜像。

```dockerfile
# ── Stage 1: 构建前端 ──────────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ .
RUN npm run build
# 产物在 /frontend/dist


# ── Stage 2: Python 运行环境 ───────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY bitget_bot/ ./bitget_bot/
COPY backtest.py .

# 从 Stage 1 复制前端构建产物
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# 创建数据目录
RUN mkdir -p data

# 暴露端口
EXPOSE 8080

# 启动命令：uvicorn 托管 FastAPI（其中包含机器人后台线程）
CMD ["uvicorn", "bitget_bot.api:app", "--host", "0.0.0.0", "--port", "8080"]
```

---

## 3. `docker-compose.yml`

```yaml
version: "3.9"

services:
  bitget-bot:
    build: .
    container_name: bitget-bot
    ports:
      - "8080:8080"          # Dashboard 访问端口
    volumes:
      - ./data:/app/data     # SQLite 数据库持久化（容器销毁数据不丢失）
    env_file:
      - .env                 # API 密钥等敏感配置
    restart: unless-stopped  # 崩溃自动重启，手动 stop 不重启
    logging:
      driver: "json-file"
      options:
        max-size: "10m"      # 日志文件最大 10MB
        max-file: "3"        # 最多保留 3 个日志文件
```

---

## 4. `.dockerignore`

避免将无关文件（虚拟环境、数据库、密钥）打入镜像：

```
.venv/
__pycache__/
*.pyc
data/
.env
frontend/node_modules/
frontend/dist/
*.db
.git/
```

---

## 5. `.env` 配置文件

```env
# ── Bitget API 密钥（真实交易时填写）──
BITGET_API_KEY=your_api_key_here
BITGET_API_SECRET=your_api_secret_here
BITGET_API_PASSPHRASE=your_passphrase_here

# ── 交易参数 ──
SYMBOL=BTC/USDT:USDT
TIMEFRAME=15m
DRY_RUN=true          # 改为 false 开启真实交易
LEVERAGE=5
MARGIN_USAGE_PCT=100.0
SQUEEZE_THRESHOLD=0.35

# ── 数据库 ──
DB_PATH=data/bot.db
```

> ⚠️ `.env` 文件绝对不能提交到 Git，确认 `.gitignore` 中包含 `.env`。

---

## 6. 服务器部署步骤

### 6.1 服务器环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Ubuntu 22.04 / Debian 12（推荐） |
| CPU | 1 核以上 |
| 内存 | 512MB 以上（1GB 推荐） |
| Docker | >= 24.0 |
| docker-compose | >= 2.0 （或 docker compose plugin） |

### 6.2 首次部署

```bash
# 1. 登录服务器，安装 Docker（如未安装）
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# 2. 上传代码到服务器（方法选一）
#    方法 A：git clone（推荐，便于后续更新）
git clone https://github.com/your-repo/Bitget.git
cd Bitget

#    方法 B：scp 本地上传
scp -r /Users/max/Developer/Bitget user@server-ip:/home/user/

# 3. 配置环境变量
cp .env.example .env
nano .env   # 填入真实 API 密钥（如需真实交易）

# 4. 构建并启动
docker compose up -d --build

# 5. 查看启动日志
docker compose logs -f
```

启动成功后访问：`http://服务器IP:8080`

### 6.3 后续更新

```bash
# 拉取最新代码
git pull

# 重新构建并重启（不影响 data/ 中的数据库）
docker compose up -d --build
```

### 6.4 常用运维命令

```bash
# 查看实时日志
docker compose logs -f bitget-bot

# 查看机器人运行状态
docker compose ps

# 进入容器调试
docker compose exec bitget-bot bash

# 停止机器人
docker compose stop

# 完全停止并删除容器（数据库 data/ 不受影响）
docker compose down

# 手动运行一次回测
docker compose exec bitget-bot python backtest.py --days 90 --leverage 5
```

---

## 7. 可选：配置 HTTPS（使用 Nginx 反向代理）

如果服务器有域名，建议通过 Nginx + Let's Encrypt 提供 HTTPS 访问：

```nginx
# /etc/nginx/sites-available/bitget
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8080;
        proxy_http_version 1.1;

        # WebSocket 支持（重要）
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

安装证书：
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

---

## 8. 安全建议

| 风险 | 建议 |
|------|------|
| API 密钥泄露 | `.env` 不提交 Git；服务器上权限设为 `600` |
| 端口暴露 | 生产环境用 Nginx 反代，不直接暴露 8080 |
| Dashboard 无认证 | 可加 HTTP Basic Auth 或 Token 认证防止他人访问 |
| 数据库备份 | 定期备份 `data/bot.db`（cron + scp 到本地） |

---

## 9. 数据备份脚本（可选）

在服务器上配置每日自动备份：

```bash
# /home/user/backup-db.sh
#!/bin/bash
DATE=$(date +%Y%m%d)
cp /home/user/Bitget/data/bot.db /home/user/backups/bot_${DATE}.db
# 只保留最近 30 天备份
find /home/user/backups -name "bot_*.db" -mtime +30 -delete
```

```bash
# 加入 crontab（每天凌晨 2 点执行）
crontab -e
0 2 * * * /home/user/backup-db.sh
```
