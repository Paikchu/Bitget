# Phase 2 — FastAPI 后端 + WebSocket

> 目标：将 Phase 1 存储的数据通过 HTTP REST API 和 WebSocket 暴露给前端，并提供实时推送能力。

---

## 1. 新增文件：`bitget_bot/api.py`

### 技术选型

| 组件 | 选择 | 原因 |
|------|------|------|
| Web 框架 | **FastAPI** | 异步、内置 WebSocket、自动生成 API 文档 |
| ASGI 服务器 | **uvicorn** | FastAPI 官方推荐 |
| 跨域 | **CORS 中间件** | 前端开发时本地地址不同源 |

### 新增依赖

```
# requirements.txt 新增
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
```

---

## 2. REST API 接口设计

### `GET /api/status`

返回机器人当前运行状态。

**响应示例：**
```json
{
  "running": true,
  "dry_run": true,
  "symbol": "BTC/USDT:USDT",
  "timeframe": "15m",
  "current_position": "long",
  "current_equity": 10234.56,
  "last_checked_at": "2024-01-15T08:30:00Z"
}
```

---

### `GET /api/trades`

返回历史交易列表，支持分页。

**查询参数：**
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `page` | int | 1 | 页码 |
| `limit` | int | 50 | 每页条数（最大200） |
| `direction` | str | - | 筛选 `long` 或 `short` |
| `closed_only` | bool | false | 只返回已平仓的交易 |

**响应示例：**
```json
{
  "total": 127,
  "page": 1,
  "limit": 50,
  "trades": [
    {
      "id": 127,
      "symbol": "BTC/USDT:USDT",
      "direction": "long",
      "entry_time": "2024-01-15T06:00:00Z",
      "entry_price": 42350.50,
      "exit_time": "2024-01-15T08:15:00Z",
      "exit_price": 43120.00,
      "pnl_pct": 1.82,
      "pnl_usdt": 182.30,
      "notional": 10000.00,
      "is_dry_run": true
    }
  ]
}
```

---

### `GET /api/equity`

返回权益曲线数据点。

**查询参数：**
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `days` | int | 30 | 返回最近 N 天的数据 |
| `resample` | str | `1h` | 采样间隔（`15m` / `1h` / `1d`） |

**响应示例：**
```json
{
  "initial_equity": 10000.0,
  "current_equity": 10234.56,
  "return_pct": 2.35,
  "max_drawdown_pct": -3.12,
  "points": [
    {"ts": "2024-01-01T00:00:00Z", "equity": 10000.0},
    {"ts": "2024-01-01T01:00:00Z", "equity": 10045.2}
  ]
}
```

---

### `GET /api/ohlcv`

返回 K 线数据（用于前端图表渲染）。

**查询参数：**
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `symbol` | str | BTC/USDT:USDT | 交易对 |
| `timeframe` | str | 15m | K 线周期 |
| `limit` | int | 200 | 返回根数 |

**响应示例：**
```json
{
  "candles": [
    {"ts": 1705276800000, "open": 42100, "high": 42500, "low": 41900, "close": 42350, "volume": 1234.5}
  ]
}
```

---

### `POST /api/backtest`

触发一次回测，返回回测结果。（异步执行，可能耗时较长）

**请求体：**
```json
{
  "symbol": "BTC/USDT:USDT",
  "days": 90,
  "squeeze": 0.35,
  "equity": 10000,
  "leverage": 5,
  "margin_pct": 100.0
}
```

**响应示例：**
```json
{
  "job_id": "bt_20240115_083000",
  "status": "running"
}
```

### `GET /api/backtest/{job_id}`

查询回测结果。

**响应示例：**
```json
{
  "status": "done",
  "summary": {
    "total_trades": 48,
    "win_rate": 62.5,
    "profit_factor": 1.84,
    "total_pnl_usdt": 2340.50,
    "total_return_pct": 23.4,
    "max_drawdown_pct": -8.2,
    "date_range": ["2023-10-15", "2024-01-15"]
  },
  "equity_curve": [
    {"ts": "2023-10-15T00:00:00Z", "equity": 10000.0}
  ],
  "trades": []
}
```

---

## 3. WebSocket 接口

### `WS /ws`

客户端连接后，服务器主动推送以下类型的消息：

**消息格式（统一结构）：**
```json
{
  "type": "事件类型",
  "data": {},
  "ts": "2024-01-15T08:30:00Z"
}
```

**事件类型清单：**

| `type` | 触发时机 | `data` 内容 |
|--------|---------|------------|
| `trade_open` | 机器人开仓 | 新交易完整字段 |
| `trade_close` | 机器人平仓 | 更新后的交易（含 PnL） |
| `equity_update` | 每次 run_cycle 结束 | `{ts, equity}` |
| `bot_status` | 状态变化 | `{running, position, ...}` |
| `signal` | 策略检测到信号 | `{type: "long_entry", bar_time, price}` |
| `error` | 运行出错 | `{message}` |

### WebSocket 推送实现方式

使用 FastAPI 的 `ConnectionManager` 模式管理多个客户端连接，`runner.py` 中在关键节点调用 `broadcast()` 函数：

```python
# 在 api.py 中定义全局广播器
class ConnectionManager:
    def __init__(self):
        self.active_connections: list = []

    async def broadcast(self, message: dict):
        for ws in self.active_connections:
            await ws.send_json(message)

manager = ConnectionManager()
```

`runner.py` 通过共享队列（`asyncio.Queue`）向 API 层发送事件，避免跨线程直接调用异步函数。

---

## 4. 启动方式

### 开发模式（本地）

```bash
# 同时启动机器人 + API 服务
uvicorn bitget_bot.api:app --host 0.0.0.0 --port 8080 --reload
```

在另一个终端启动机器人：
```bash
python -m bitget_bot.runner
```

### 生产模式（服务器）

通过 `api.py` 的 lifespan 事件在同一进程内以后台线程启动 runner，做到一个进程管理所有逻辑：

```python
from contextlib import asynccontextmanager
import threading

@asynccontextmanager
async def lifespan(app):
    t = threading.Thread(target=run_bot_loop, daemon=True)
    t.start()
    yield
```

API 文档访问地址（FastAPI 自动生成）：
```
http://服务器IP:8080/docs
```

---

## 5. 验证方式

Phase 2 完成后，可用以下方式验证：

```bash
# 测试 status 接口
curl http://localhost:8080/api/status

# 测试 trades 接口
curl http://localhost:8080/api/trades?limit=5

# 测试 WebSocket（需要 wscat 工具）
npm install -g wscat
wscat -c ws://localhost:8080/ws
```

也可直接访问 `http://localhost:8080/docs` 查看交互式 API 文档。
