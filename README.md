# Bitget Bot

一个面向 Bitget USDT 永续合约的量化交易项目，当前已经包含：

- Python 交易机器人
- FastAPI 后端接口
- React + Vite 可视化面板
- SQLite 数据持久化
- Docker 沙箱策略回测
- Strategy Studio 策略生成、校验、修复与版本管理

项目目前采用单体部署方式：前端在构建后由 FastAPI 直接托管，运行时通过后台线程启动交易轮询，策略回测通过独立 Docker 沙箱执行。

## 当前功能

### 交易与监控

- 基于 Bitget 合约市场轮询已收盘 K 线并执行策略
- 支持 `DRY_RUN=true` 的模拟模式
- 实时查看机器人状态、持仓方向、杠杆、收益率
- 查看交易记录、权益曲线、事件日志
- 分页加载 OHLCV/K 线数据
- WebSocket 推送交易、信号、权益更新

### Strategy Studio

- 内置默认策略加载
- Markdown 策略描述生成 Python 策略代码
- 策略静态安全校验
- 策略运行时错误后使用 AI 自动修复
- 通过 Docker 沙箱执行策略回测
- 记录策略版本历史与最近一次回测摘要
- 支持恢复历史版本到编辑器

### 配置管理

- 通过界面读取和更新运行配置
- 敏感字段脱敏展示
- 支持测试 Bitget 和 DeepSeek 连接

## 技术栈

### 后端

- Python 3.11
- FastAPI
- Uvicorn
- SQLite
- ccxt
- pandas / numpy
- OpenAI Python SDK（用于调用 DeepSeek 接口）

### 前端

- React 19
- Vite
- Tailwind CSS 4
- SWR
- Zustand
- Monaco Editor
- lightweight-charts
- Recharts

### 运行与隔离

- Docker
- Docker Compose

## 快速启动

### 1. 准备环境变量

复制示例配置：

```bash
cp .env.example .env
```

至少确认以下字段：

- `SYMBOL`
- `TIMEFRAME`
- `DRY_RUN`
- `LEVERAGE`
- `INITIAL_EQUITY`
- `DB_PATH`

如果要启用真实交易，需要补齐：

- `BITGET_API_KEY`
- `BITGET_API_SECRET`
- `BITGET_API_PASSPHRASE`

如果要启用 Strategy Studio 的 AI 生成和修复，需要补齐：

- `DEEPSEEK_API_KEY`

### 2. 使用 Docker Compose 启动

这是当前项目最完整、最接近实际交付方式的启动方式：

```bash
make docker-up
```

启动后访问：

- 应用首页: [http://127.0.0.1:8080](http://127.0.0.1:8080)
- API 文档: [http://127.0.0.1:8080/docs](http://127.0.0.1:8080/docs)

常用命令：

```bash
make docker-ps
make docker-logs
make docker-restart
make docker-down
```

### 3. 本地开发启动

先构建沙箱镜像：

```bash
make sandbox
```

然后启动后端开发服务：

```bash
make dev
```

说明：

- 本地开发模式默认启动 `uvicorn bitget_bot.api:app --reload`
- 前端产物由 Docker 构建阶段生成，当前根目录并没有集成一套独立的前后端联调脚本
- 如果你修改了前端界面，按当前项目约定应优先执行 `make docker-up` 重新构建镜像，而不是只重启容器

## 环境变量

项目当前使用的核心环境变量如下：

| 变量 | 说明 |
| --- | --- |
| `BITGET_API_KEY` | Bitget API Key，真实交易必填 |
| `BITGET_API_SECRET` | Bitget API Secret，真实交易必填 |
| `BITGET_API_PASSPHRASE` | Bitget API Passphrase，真实交易必填 |
| `SYMBOL` | 交易对，默认 `BTC/USDT:USDT` |
| `TIMEFRAME` | K 线周期，默认 `15m` |
| `DRY_RUN` | 是否模拟交易，默认 `true` |
| `LEVERAGE` | 杠杆倍数 |
| `MARGIN_USAGE_PCT` | 使用保证金比例 |
| `SQUEEZE_THRESHOLD` | 策略挤压阈值 |
| `INITIAL_EQUITY` | 初始权益，主要用于收益跟踪与回测 |
| `DB_PATH` | SQLite 数据库路径，默认 `data/bot.db` |
| `POLL_INTERVAL` | 轮询周期，单位秒 |
| `DEEPSEEK_API_KEY` | Strategy Studio AI 能力所需密钥 |

参考文件：[`/Users/max/Developer/Bitget/.env.example`](/Users/max/Developer/Bitget/.env.example)

## 项目结构

```text
.
├── bitget_bot/              # 后端主包
│   ├── api.py               # FastAPI 应用入口
│   ├── runner.py            # 交易轮询与下单逻辑
│   ├── db.py                # SQLite 持久化
│   ├── settings_manager.py  # 配置读取、保存、连接测试
│   ├── strategy_router.py   # Strategy Studio 接口
│   ├── sandbox/             # 策略代码校验与 Docker 执行
│   └── strategies/          # 内置策略
├── frontend/                # React 前端
├── sandbox/                 # 沙箱镜像与运行脚本
├── tests/                   # pytest 测试
├── docs/                    # 阶段文档与实现计划
├── data/                    # SQLite 数据目录
├── backtest.py              # 独立回测脚本
├── Dockerfile               # 多阶段构建镜像
├── docker-compose.yml       # 运行编排
└── Makefile                 # 常用开发命令
```

## 接口概览

### 后端 API

当前代码中已经实现的主要接口：

- `GET /api/status`
- `GET /api/settings`
- `PUT /api/settings`
- `POST /api/settings/test`
- `GET /api/trades`
- `GET /api/equity`
- `GET /api/ohlcv`
- `GET /api/events`
- `POST /api/backtest`
- `GET /api/backtest/{job_id}`

### Strategy Studio API

- `POST /api/strategy/generate`
- `GET /api/strategy/versions`
- `GET /api/strategy/versions/{version_id}`
- `POST /api/strategy/backtest`
- `GET /api/strategy/backtest/{job_id}`
- `GET /api/strategy/builtin`
- `GET /api/strategy/builtin/{strategy_id}`
- `POST /api/strategy/validate`
- `POST /api/strategy/fix`

### WebSocket

- `GET /ws`

用于推送：

- `trade_open`
- `trade_close`
- `signal`
- `equity_update`

## Docker 说明

项目当前有两类 Docker 用途：

### 应用主容器

- 使用根目录 [`/Users/max/Developer/Bitget/Dockerfile`](/Users/max/Developer/Bitget/Dockerfile)
- 多阶段构建前端并打包到 Python 运行时镜像
- 默认暴露 `8080`
- `docker-compose.yml` 挂载本地 `./data` 到容器内 `/app/data`

### 策略沙箱容器

- 由 `make sandbox` 构建
- 供 Strategy Studio 回测时执行用户生成的策略代码
- 主应用容器通过挂载 `/var/run/docker.sock` 调用 Docker 运行沙箱

这意味着运行 Strategy Studio 回测时，宿主机必须可用 Docker。

## 测试

后端当前已经覆盖的测试方向包括：

- API 接口
- 设置读写与连接测试
- OHLCV 分页
- 策略版本管理
- 策略生成逻辑
- AST 安全校验
- Docker 执行器
- 沙箱运行器
- 策略迁移一致性

执行测试：

```bash
pytest
```

如果要跑依赖 Docker 的集成测试，需先确保：

- Docker Daemon 正常运行
- 已构建 `strategy-sandbox:latest`

示例：

```bash
pytest -m integration
```

## 独立回测脚本

项目根目录提供了一个独立脚本 [`/Users/max/Developer/Bitget/backtest.py`](/Users/max/Developer/Bitget/backtest.py)，可直接基于 Bitget 公共 API 拉取历史数据并执行回测。

示例：

```bash
python backtest.py --help
```

适合：

- 验证默认策略
- 快速查看回测统计
- 导出交易与权益结果

## 当前运行方式说明

从代码现状看，项目更适合作为“单实例部署的策略监控与实验平台”使用，而不是多服务拆分架构：

- FastAPI 在启动时会自动初始化数据库并启动机器人后台线程
- 前端构建产物由后端直接托管
- SQLite 用于本地持久化
- 回测任务结果存于内存，服务重启后不会保留
- Strategy Studio 的版本历史与最近回测摘要会写入 SQLite

## 相关文档

- [`/Users/max/Developer/Bitget/docs/README.md`](/Users/max/Developer/Bitget/docs/README.md)
- [`/Users/max/Developer/Bitget/docs/phase1-database.md`](/Users/max/Developer/Bitget/docs/phase1-database.md)
- [`/Users/max/Developer/Bitget/docs/phase2-api.md`](/Users/max/Developer/Bitget/docs/phase2-api.md)
- [`/Users/max/Developer/Bitget/docs/phase3-frontend.md`](/Users/max/Developer/Bitget/docs/phase3-frontend.md)
- [`/Users/max/Developer/Bitget/docs/phase4-deployment.md`](/Users/max/Developer/Bitget/docs/phase4-deployment.md)
- [`/Users/max/Developer/Bitget/docs/strategy-studio-test-flow.md`](/Users/max/Developer/Bitget/docs/strategy-studio-test-flow.md)

## README 编写依据

这份 README 参考了通用的仓库说明最佳实践，重点保留了以下信息：

- 项目用途
- 当前已完成能力
- 快速启动路径
- 配置方法
- 目录结构
- 接口与测试入口

参考资料：

- [GitHub Docs: About the repository README file](https://docs.github.com/articles/about-readmes)
- [GitHub Docs: Best practices for repositories](https://docs.github.com/repositories/creating-and-managing-repositories/best-practices-for-repositories)
- [Make a README](https://www.makeareadme.com/)
