# Phase 1 — 数据库层 + Runner 改造

> 目标：让机器人的每一次交易、信号、状态变化都被持久化存储，为后续 API 和前端提供数据基础。

---

## 1. 新增文件：`bitget_bot/db.py`

负责所有 SQLite 的初始化和读写操作。

### 数据库表结构

#### `trades` 表 — 交易记录

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK | 自增主键 |
| `symbol` | TEXT | 交易对，如 BTC/USDT:USDT |
| `direction` | TEXT | `long` 或 `short` |
| `entry_time` | TEXT | 开仓时间（ISO 8601 UTC） |
| `entry_price` | REAL | 开仓价格 |
| `exit_time` | TEXT | 平仓时间（NULL = 仍持仓） |
| `exit_price` | REAL | 平仓价格（NULL = 仍持仓） |
| `pnl_pct` | REAL | 价格涨跌幅 % |
| `pnl_usdt` | REAL | 实际盈亏 USDT |
| `notional` | REAL | 开仓名义价值 |
| `is_dry_run` | INTEGER | 1=模拟交易，0=真实交易 |
| `created_at` | TEXT | 记录创建时间 |

#### `equity_snapshots` 表 — 权益快照

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK | 自增主键 |
| `ts` | TEXT | 快照时间（对应 K 线时间） |
| `equity` | REAL | 当时账户权益 USDT |
| `created_at` | TEXT | 写入时间 |

#### `bot_events` 表 — 运行日志

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK | 自增主键 |
| `event_type` | TEXT | `signal` / `order` / `error` / `info` |
| `message` | TEXT | 事件描述 |
| `payload` | TEXT | JSON 格式的额外数据 |
| `created_at` | TEXT | 事件时间 |

### 代码实现

```python
# bitget_bot/db.py
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/bot.db")

def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """建表（幂等）。"""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                direction   TEXT    NOT NULL,
                entry_time  TEXT    NOT NULL,
                entry_price REAL    NOT NULL,
                exit_time   TEXT,
                exit_price  REAL,
                pnl_pct     REAL,
                pnl_usdt    REAL,
                notional    REAL,
                is_dry_run  INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT    NOT NULL,
                equity     REAL    NOT NULL,
                created_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT    NOT NULL,
                message    TEXT    NOT NULL,
                payload    TEXT,
                created_at TEXT    NOT NULL
            );
        """)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def insert_trade_open(symbol, direction, entry_time, entry_price, notional, is_dry_run) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO trades (symbol, direction, entry_time, entry_price,
               notional, is_dry_run, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (symbol, direction, entry_time, entry_price, notional, int(is_dry_run), _now())
        )
        return cur.lastrowid

def close_trade(trade_id, exit_time, exit_price, pnl_pct, pnl_usdt) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE trades SET exit_time=?, exit_price=?, pnl_pct=?, pnl_usdt=?
               WHERE id=?""",
            (exit_time, exit_price, pnl_pct, pnl_usdt, trade_id)
        )

def insert_equity(ts: str, equity: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO equity_snapshots (ts, equity, created_at) VALUES (?,?,?)",
            (ts, equity, _now())
        )

def log_event(event_type: str, message: str, payload: dict = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO bot_events (event_type, message, payload, created_at) VALUES (?,?,?,?)",
            (event_type, message, json.dumps(payload) if payload else None, _now())
        )
```

---

## 2. 改造 `runner.py`

在 `run_cycle()` 关键节点写入数据库。

### 改造点

1. **模块顶部**：`init_db()` 在 `main()` 启动时调用一次
2. **`run_cycle()` 开头**：记录策略信号到 `bot_events`
3. **检测到开仓信号时**：调用 `insert_trade_open()` 并保存返回的 `trade_id`
4. **检测到平仓信号时**：调用 `close_trade()` 完成记录
5. **每次循环结束**：调用 `insert_equity()` 记录当前权益快照

### 需要新增的状态变量

`runner.py` 的 `main()` 循环中需要维护两个额外变量：
- `current_trade_id: Optional[int]` — 当前持仓对应的数据库 ID
- `current_equity: float` — 当前权益（从 Bitget 余额读取）

---

## 3. 新增配置

`.env` 文件新增一行：

```env
DB_PATH=data/bot.db   # 可选，默认值即 data/bot.db
```

---

## 4. 验证方式

Phase 1 完成后，运行机器人一段时间，然后用以下命令检查数据是否正常写入：

```bash
sqlite3 data/bot.db "SELECT * FROM trades LIMIT 10;"
sqlite3 data/bot.db "SELECT * FROM bot_events ORDER BY id DESC LIMIT 20;"
sqlite3 data/bot.db "SELECT COUNT(*) FROM equity_snapshots;"
```

---

## 依赖变化

`requirements.txt` 无需新增依赖，SQLite 是 Python 标准库自带。

只需新建 `data/` 目录（`.gitignore` 中加入 `data/*.db`）。
