# Strategy Studio — 自然语言策略工作台 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在现有 Bitget 交易平台上新增 "Strategy Studio" 标签页，支持用户用 Markdown 描述策略，通过 DeepSeek API 翻译为 Python 代码，在 Docker 沙箱中安全回测，并可部署为实盘策略。

**Architecture:** 主应用（FastAPI + React）新增三条 API 路由：`/api/strategy/generate`（调用 DeepSeek）、`/api/strategy/backtest`（向 Docker 沙箱容器提交任务）、`/api/strategy/backtest/{job_id}`（轮询结果）。沙箱是一个独立的最小化 Docker 镜像，通过 stdin/stdout 与主进程通信，无网络访问权限，有严格的内存/CPU 限制，代码进入沙箱前需经过 AST 白名单静态检查。前端新增 Strategy Studio 页面，左侧 Monaco Markdown 编辑器，右侧 Monaco Python 编辑器（可手动修改 AI 生成代码），底部展示详细回测结果（权益曲线 + 交易列表 + 统计摘要）。

**Tech Stack:**
- Backend: Python 3.11, FastAPI, Docker SDK (`docker` 包), DeepSeek API (OpenAI 兼容), `ast` 模块
- Frontend: React 18, Monaco Editor (`@monaco-editor/react`), Recharts（已有），TailwindCSS（已有）
- Infrastructure: Docker（已有）, Docker socket mount，新增 `strategy-sandbox` 镜像

---

## 整体架构图

```
浏览器
  │
  │  POST /api/strategy/generate
  │  POST /api/strategy/backtest
  │  GET  /api/strategy/backtest/{job_id}
  ▼
┌──────────────────────────────────────────────────────────┐
│  bitget-bot 主容器 (FastAPI)                              │
│                                                          │
│  strategy_router.py                                      │
│  ├── /generate  → DeepSeek API (外网)                    │
│  └── /backtest  → docker_executor.py                    │
│                        │                                │
│                        │ docker run --rm               │
│                        │ stdin=OHLCV+代码              │
│                        │ stdout=结果JSON               │
│                        ▼                                │
│           ┌────────────────────────┐                    │
│           │  strategy-sandbox 容器  │                    │
│           │  ─ 无网络               │                    │
│           │  ─ 无文件写入           │                    │
│           │  ─ 内存≤512MB          │                    │
│           │  ─ CPU≤1核             │                    │
│           │  ─ 超时120s            │                    │
│           │  sandbox_runner.py     │                    │
│           └────────────────────────┘                    │
│                                                          │
│  主容器挂载 /var/run/docker.sock                          │
└──────────────────────────────────────────────────────────┘
```

## 沙箱策略接口规范

用户策略代码（AI 生成或手写）必须实现以下两个函数：

```python
import numpy as np
import pandas as pd

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    输入: OHLCV DataFrame，列名: open, high, low, close, volume, timestamp
    输出: 同一 DataFrame，添加了指标列（任意命名）
    """
    out = df.copy()
    out['sma20'] = out['close'].rolling(20).mean()
    return out

def get_signal(df: pd.DataFrame, i: int, params: dict) -> dict:
    """
    输入: 含指标的 DataFrame，当前 bar 索引 i，参数字典
    输出: 信号字典，必须包含以下 4 个布尔 key
    """
    return {
        'long_entry':   bool,   # 开多
        'short_entry':  bool,   # 开空
        'close_long':   bool,   # 平多
        'close_short':  bool,   # 平空
    }
```

沙箱回测引擎（`sandbox_runner.py` 内置）负责调用这两个函数逐 bar 模拟。

---

## Task 1: 构建 strategy-sandbox Docker 镜像

**Files:**
- Create: `sandbox/Dockerfile.sandbox`
- Create: `sandbox/sandbox_runner.py`

### Step 1: 创建沙箱 Dockerfile

```dockerfile
# sandbox/Dockerfile.sandbox
FROM python:3.11-slim

WORKDIR /sandbox

# 只安装策略必需的库，不安装 ccxt（禁止策略代码联网）
RUN pip install --no-cache-dir numpy==1.26.4 pandas==2.2.2

# 创建无权限用户（非 root 运行）
RUN useradd -m -u 1000 sandbox
USER sandbox

COPY sandbox_runner.py .

# stdin → 运行 → stdout
ENTRYPOINT ["python", "sandbox_runner.py"]
```

### Step 2: 创建沙箱运行脚本

```python
# sandbox/sandbox_runner.py
"""
沙箱回测运行器 —— 在隔离容器内执行用户策略代码。

输入 (stdin): JSON
  {
    "strategy_code": "...",        # 用户策略 Python 代码字符串
    "ohlcv": [[ts,o,h,l,c,v],...], # OHLCV 数组
    "params": {
      "squeeze_threshold": 0.35,
      "initial_equity": 10000.0,
      "leverage": 5,
      "margin_pct": 100.0,
      "fee_rate": 0.0005
    }
  }

输出 (stdout): JSON
  {
    "success": true,
    "summary": {...},
    "equity_curve": [...],
    "trades": [...]
  }
"""
from __future__ import annotations

import json
import math
import resource
import sys
import types
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

# ── 资源限制（在容器内再加一层软性限制）──────────────────────
MB = 1024 * 1024
try:
    resource.setrlimit(resource.RLIMIT_AS,  (512 * MB, 512 * MB))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
except Exception:
    pass  # Docker 的 --memory 和 --cpus 已经在外层限制

# ── 受限 builtins（不暴露危险函数）──────────────────────────
_SAFE_BUILTINS = {
    k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
    for k in [
        "print", "range", "len", "int", "float", "str", "bool",
        "list", "dict", "tuple", "set", "frozenset",
        "min", "max", "sum", "abs", "round", "sorted", "reversed",
        "enumerate", "zip", "map", "filter", "any", "all",
        "isinstance", "issubclass", "hasattr", "getattr", "setattr",
        "ValueError", "TypeError", "IndexError", "KeyError",
        "AttributeError", "Exception", "StopIteration",
        "NotImplementedError", "RuntimeError", "ZeroDivisionError",
        "True", "False", "None",
        "__name__", "__build_class__",
    ]
    if hasattr(__builtins__ if not isinstance(__builtins__, dict) else type('', (), __builtins__)(), k)
    or (isinstance(__builtins__, dict) and k in __builtins__)
}


def _load_strategy(code: str):
    """在受限环境中加载用户策略代码，返回 (add_indicators, get_signal)。"""
    module = types.ModuleType("user_strategy")
    module.__dict__["__builtins__"] = _SAFE_BUILTINS
    module.__dict__["np"] = np
    module.__dict__["pd"] = pd
    module.__dict__["math"] = math

    try:
        exec(compile(code, "<strategy>", "exec"), module.__dict__)
    except Exception as e:
        raise ValueError(f"策略代码执行错误: {e}")

    if not callable(getattr(module, "add_indicators", None)):
        raise ValueError("策略代码必须定义 add_indicators(df) 函数")
    if not callable(getattr(module, "get_signal", None)):
        raise ValueError("策略代码必须定义 get_signal(df, i, params) 函数")

    return module.add_indicators, module.get_signal


@dataclass
class _Trade:
    direction: str
    entry_bar: int
    entry_time: str
    entry_price: float
    exit_bar: Optional[int] = None
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    pnl_usdt: Optional[float] = None
    notional: Optional[float] = None
    fee_usdt: Optional[float] = None


def _run_backtest(df: pd.DataFrame, add_indicators, get_signal, params: dict):
    squeeze_threshold = params.get("squeeze_threshold", 0.35)
    initial_equity = params.get("initial_equity", 10_000.0)
    leverage = params.get("leverage", 5)
    margin_pct = params.get("margin_pct", 100.0)
    fee_rate = params.get("fee_rate", 0.0005)

    d = add_indicators(df.copy())
    times = pd.to_datetime(d["timestamp"], unit="ms", utc=True)
    n = len(d)

    equity = initial_equity
    position = 0
    trades: list[_Trade] = []
    equity_arr = np.full(n, np.nan)
    equity_arr[0] = equity

    for i in range(1, n - 1):
        try:
            sig = get_signal(d, i, {"squeeze_threshold": squeeze_threshold})
        except Exception:
            equity_arr[i] = equity
            continue

        long_entry  = bool(sig.get("long_entry",  False))
        short_entry = bool(sig.get("short_entry", False))
        close_long  = bool(sig.get("close_long",  False))
        close_short = bool(sig.get("close_short", False))

        # 计算新仓位
        new_pos = position
        if long_entry:  new_pos = 1
        if short_entry: new_pos = -1
        if position > 0 and close_long:  new_pos = 0
        if position < 0 and close_short: new_pos = 0

        fill_price = float(d["open"].iloc[i + 1])
        fill_time  = times.iloc[i + 1].isoformat()
        fill_bar   = i + 1

        # 平仓
        if position != 0 and new_pos != position:
            t = trades[-1]
            t.exit_bar = fill_bar
            t.exit_time = fill_time
            t.exit_price = fill_price
            ret = (fill_price - t.entry_price) / t.entry_price
            if t.direction == "short":
                ret = -ret
            t.pnl_pct = ret * 100.0
            fees = (t.notional or 0) * fee_rate * 2
            t.fee_usdt = round(fees, 6)
            t.pnl_usdt = (t.notional or 0) * ret - fees
            equity += t.pnl_usdt
            position = 0

        # 开仓
        if new_pos != 0 and new_pos != position:
            notional = equity * (margin_pct / 100.0) * leverage
            trades.append(_Trade(
                direction="long" if new_pos == 1 else "short",
                entry_bar=fill_bar,
                entry_time=fill_time,
                entry_price=fill_price,
                notional=notional,
            ))
            position = new_pos

        equity_arr[i + 1] = equity

    # 强制平末仓
    if position != 0 and trades and trades[-1].exit_price is None:
        t = trades[-1]
        t.exit_bar = n - 1
        t.exit_time = times.iloc[n - 1].isoformat()
        t.exit_price = float(d["close"].iloc[n - 1])
        ret = (t.exit_price - t.entry_price) / t.entry_price
        if t.direction == "short": ret = -ret
        t.pnl_pct = ret * 100.0
        fees = (t.notional or 0) * fee_rate * 2
        t.fee_usdt = round(fees, 6)
        t.pnl_usdt = (t.notional or 0) * ret - fees
        equity += t.pnl_usdt

    # 前向填充 equity
    last = initial_equity
    for i in range(n):
        if math.isnan(equity_arr[i]):
            equity_arr[i] = last
        else:
            last = equity_arr[i]

    return trades, equity_arr, times


def _build_result(trades, equity_arr, times, initial_equity, req_params):
    closed = [t for t in trades if t.pnl_usdt is not None]
    pnls   = [t.pnl_usdt for t in closed]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = sum(pnls) if pnls else 0.0

    peak = np.maximum.accumulate(equity_arr)
    dd   = (equity_arr - peak) / np.where(peak > 0, peak, 1) * 100.0

    eq_series = list(zip(times.tolist(), equity_arr.tolist()))
    step = max(1, len(eq_series) // 300)  # 下采样，最多 300 个点
    eq_downsampled = [
        {"ts": str(ts), "equity": round(eq, 2)}
        for ts, eq in eq_series[::step]
    ]

    return {
        "success": True,
        "summary": {
            "total_trades": len(closed),
            "long_trades":  sum(1 for t in closed if t.direction == "long"),
            "short_trades": sum(1 for t in closed if t.direction == "short"),
            "wins":     len(wins),
            "losses":   len(losses),
            "win_rate_pct": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
            "profit_factor": (
                round(sum(wins) / abs(sum(losses)), 2)
                if losses and sum(losses) != 0 else None
            ),
            "total_pnl_usdt":   round(total_pnl, 2),
            "total_return_pct": round(total_pnl / initial_equity * 100, 2),
            "total_fee_usdt":   round(sum(t.fee_usdt or 0 for t in closed), 2),
            "initial_equity":   initial_equity,
            "final_equity":     round(initial_equity + total_pnl, 2),
            "max_drawdown_pct": round(float(np.min(dd)), 2),
            "date_from": str(times.iloc[0])  if len(times) else None,
            "date_to":   str(times.iloc[-1]) if len(times) else None,
        },
        "equity_curve": eq_downsampled,
        "trades": [
            {
                "direction":   t.direction,
                "entry_time":  t.entry_time,
                "entry_price": t.entry_price,
                "exit_time":   t.exit_time,
                "exit_price":  t.exit_price,
                "pnl_pct":     round(t.pnl_pct, 4) if t.pnl_pct is not None else None,
                "pnl_usdt":    round(t.pnl_usdt, 2),
                "fee_usdt":    round(t.fee_usdt or 0, 4),
                "notional":    round(t.notional or 0, 2),
            }
            for t in closed
        ],
    }


def main():
    try:
        payload = json.loads(sys.stdin.read())
        strategy_code = payload["strategy_code"]
        ohlcv         = payload["ohlcv"]
        params        = payload.get("params", {})

        # 加载策略
        add_indicators, get_signal = _load_strategy(strategy_code)

        # 构建 DataFrame
        df = pd.DataFrame(
            ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

        # 运行回测
        trades, equity_arr, times = _run_backtest(
            df, add_indicators, get_signal, params
        )

        # 输出结果
        result = _build_result(trades, equity_arr, times, params.get("initial_equity", 10_000.0), params)
        print(json.dumps(result))

    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
```

### Step 3: 构建沙箱镜像

```bash
cd /Users/max/Developer/Bitget
docker build -f sandbox/Dockerfile.sandbox -t strategy-sandbox:latest sandbox/
```

预期输出：`Successfully tagged strategy-sandbox:latest`

### Step 4: 冒烟测试沙箱

```bash
echo '{"strategy_code":"import numpy as np\nimport pandas as pd\n\ndef add_indicators(df):\n    out=df.copy()\n    out[\"sma20\"]=out[\"close\"].rolling(20).mean()\n    return out\n\ndef get_signal(df,i,params):\n    if i<20: return {\"long_entry\":False,\"short_entry\":False,\"close_long\":False,\"close_short\":False}\n    c=df[\"close\"].iloc[i]; s=df[\"sma20\"].iloc[i]\n    return {\"long_entry\":c>s,\"short_entry\":c<s,\"close_long\":c<s,\"close_short\":c>s}","ohlcv":[[1700000000000,35000,35100,34900,35050,100],[1700060000000,35050,35200,35000,35150,120],[1700120000000,35150,35300,35100,35200,90]],"params":{"initial_equity":10000,"leverage":5}}' \
| docker run --rm -i --network=none --memory=512m --cpus=1 strategy-sandbox:latest
```

预期：输出包含 `"success": true` 的 JSON（交易数可能为 0，因为数据点不足 20 根）

### Step 5: Commit

```bash
git add sandbox/
git commit -m "feat: add strategy-sandbox Docker image with backtest runner"
```

---

## Task 2: AST 静态代码验证器

**Files:**
- Create: `bitget_bot/sandbox/__init__.py`
- Create: `bitget_bot/sandbox/ast_validator.py`
- Create: `tests/test_ast_validator.py`

### Step 1: 写失败测试

```python
# tests/test_ast_validator.py
import pytest
from bitget_bot.sandbox.ast_validator import validate_strategy_code

def test_allows_numpy_pandas():
    code = "import numpy as np\nimport pandas as pd\n"
    errors = validate_strategy_code(code)
    assert errors == []

def test_blocks_os_import():
    code = "import os\n"
    errors = validate_strategy_code(code)
    assert any("os" in e for e in errors)

def test_blocks_subprocess():
    code = "import subprocess\n"
    errors = validate_strategy_code(code)
    assert len(errors) > 0

def test_blocks_exec_call():
    code = "exec('import os')\n"
    errors = validate_strategy_code(code)
    assert any("exec" in e for e in errors)

def test_blocks_dunder_access():
    code = "x = (1).__class__.__bases__\n"
    errors = validate_strategy_code(code)
    assert any("__bases__" in e or "__class__" in e for e in errors)

def test_blocks_open():
    code = "open('/etc/passwd')\n"
    errors = validate_strategy_code(code)
    assert len(errors) > 0

def test_catches_syntax_error():
    code = "def foo(\n"
    errors = validate_strategy_code(code)
    assert any("语法" in e or "SyntaxError" in e for e in errors)

def test_requires_add_indicators():
    code = "import numpy as np\ndef get_signal(df, i, p): return {}\n"
    errors = validate_strategy_code(code)
    assert any("add_indicators" in e for e in errors)

def test_requires_get_signal():
    code = "import numpy as np\ndef add_indicators(df): return df\n"
    errors = validate_strategy_code(code)
    assert any("get_signal" in e for e in errors)
```

### Step 2: 运行确认失败

```bash
cd /Users/max/Developer/Bitget
python -m pytest tests/test_ast_validator.py -v
```

预期：`ModuleNotFoundError: No module named 'bitget_bot.sandbox'`

### Step 3: 实现验证器

```python
# bitget_bot/sandbox/__init__.py
# (空文件)

# bitget_bot/sandbox/ast_validator.py
"""
策略代码 AST 静态分析 —— 第一道安全门。

使用白名单策略：只允许已知安全的导入，其余一律拒绝。
注意：这只是一道辅助门，Docker 容器隔离才是主要安全边界。
"""
from __future__ import annotations

import ast
from typing import List

# 允许导入的顶级模块白名单
ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "numpy", "np",
    "pandas", "pd",
    "math", "cmath",
    "statistics",
    "dataclasses",
    "typing",
    "collections",
    "functools",
    "itertools",
    "operator",
    "datetime",
    "decimal",
    "fractions",
    "numbers",
    "abc",
    "copy",
})

# 明确禁止的内置函数调用
FORBIDDEN_BUILTINS: frozenset[str] = frozenset({
    "exec", "eval", "compile", "open",
    "__import__", "breakpoint", "input",
    "memoryview", "vars", "dir",
})

# 禁止访问的危险属性（class introspection 逃逸路径）
FORBIDDEN_ATTRS: frozenset[str] = frozenset({
    "__class__", "__bases__", "__subclasses__",
    "__globals__", "__builtins__", "__code__",
    "__loader__", "__spec__", "__file__",
    "__dict__", "__module__",
    "f_locals", "f_globals", "f_back",
    "tb_frame", "tb_next",
    "gi_frame", "gi_code",
    "co_consts", "co_code", "co_filename",
})


class _StrategyValidator(ast.NodeVisitor):
    def __init__(self):
        self.errors: List[str] = []
        self._defined_functions: set[str] = set()

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top not in ALLOWED_IMPORTS:
                self.errors.append(
                    f"第 {node.lineno} 行: 不允许导入 '{alias.name}'（白名单外模块）"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        top = (node.module or "").split(".")[0]
        if top not in ALLOWED_IMPORTS:
            self.errors.append(
                f"第 {node.lineno} 行: 不允许 'from {node.module} import ...'（白名单外模块）"
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BUILTINS:
            self.errors.append(
                f"第 {node.lineno} 行: 禁止调用 '{node.func.id}()'"
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr in FORBIDDEN_ATTRS:
            self.errors.append(
                f"第 {node.lineno} 行: 禁止访问属性 '_{node.attr}_'（潜在逃逸路径）"
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._defined_functions.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.errors.append(
            f"第 {node.lineno} 行: 不允许使用 async/await（策略必须是同步代码）"
        )
        self.generic_visit(node)


def validate_strategy_code(source: str) -> List[str]:
    """
    对策略代码做静态分析。
    返回: 错误信息列表，空列表表示通过。

    重要: 这是第一道门（快速拒绝明显危险代码），
    不能替代 Docker 容器隔离。
    """
    # 语法检查
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"语法错误 (第 {e.lineno} 行): {e.msg}"]

    validator = _StrategyValidator()
    validator.visit(tree)

    # 检查必须存在的函数
    if "add_indicators" not in validator._defined_functions:
        validator.errors.append(
            "缺少必须的函数 'add_indicators(df)'"
        )
    if "get_signal" not in validator._defined_functions:
        validator.errors.append(
            "缺少必须的函数 'get_signal(df, i, params)'"
        )

    return validator.errors
```

### Step 4: 运行确认通过

```bash
python -m pytest tests/test_ast_validator.py -v
```

预期：所有测试 PASS

### Step 5: Commit

```bash
git add bitget_bot/sandbox/ tests/test_ast_validator.py
git commit -m "feat: add AST strategy code validator with whitelist"
```

---

## Task 3: Docker 沙箱执行器

**Files:**
- Create: `bitget_bot/sandbox/docker_executor.py`
- Create: `tests/test_docker_executor.py`

### Step 1: 写失败测试

```python
# tests/test_docker_executor.py
"""
集成测试：需要 Docker daemon 运行且 strategy-sandbox 镜像已构建。
运行: pytest tests/test_docker_executor.py -v -m integration
"""
import pytest
from bitget_bot.sandbox.docker_executor import run_strategy_in_sandbox, SandboxError

MINIMAL_STRATEGY = """
import numpy as np
import pandas as pd

def add_indicators(df):
    out = df.copy()
    out['sma5'] = out['close'].rolling(5).mean()
    return out

def get_signal(df, i, params):
    if i < 5 or pd.isna(df['sma5'].iloc[i]):
        return {'long_entry': False, 'short_entry': False,
                'close_long': False, 'close_short': False}
    c = df['close'].iloc[i]
    s = df['sma5'].iloc[i]
    return {
        'long_entry':  c > s,
        'short_entry': c < s,
        'close_long':  c < s,
        'close_short': c > s,
    }
"""

MALICIOUS_STRATEGY = """
import os
def add_indicators(df): return df
def get_signal(df, i, p): return {}
"""


@pytest.mark.integration
def test_valid_strategy_runs():
    # 提供足够多的 bar（>5 根）
    import time
    ohlcv = []
    base_ts = int(time.time() * 1000) - 100 * 900_000
    for k in range(50):
        ts = base_ts + k * 900_000
        ohlcv.append([ts, 35000 + k * 10, 35050 + k * 10,
                       34950 + k * 10, 35025 + k * 10, 100 + k])
    result = run_strategy_in_sandbox(
        strategy_code=MINIMAL_STRATEGY,
        ohlcv=ohlcv,
        params={"initial_equity": 10000, "leverage": 5,
                "fee_rate": 0.0005, "margin_pct": 100},
    )
    assert result["success"] is True
    assert "summary" in result
    assert "equity_curve" in result
    assert "trades" in result


@pytest.mark.integration
def test_malicious_code_blocked_by_ast():
    """恶意代码在 AST 阶段即被拒绝，不会进入 Docker。"""
    with pytest.raises(SandboxError, match="代码安全检查"):
        run_strategy_in_sandbox(
            strategy_code=MALICIOUS_STRATEGY,
            ohlcv=[],
            params={},
        )


@pytest.mark.integration
def test_timeout_kills_infinite_loop():
    loop_strategy = """
import numpy as np, pandas as pd
def add_indicators(df): return df
def get_signal(df, i, params):
    while True: pass   # 死循环
    return {}
"""
    # 需要先通过 AST（上面代码无违规），然后在容器内超时
    with pytest.raises(SandboxError, match="超时"):
        run_strategy_in_sandbox(
            strategy_code=loop_strategy,
            ohlcv=[[1, 1, 1, 1, 1, 1]] * 30,
            params={"initial_equity": 1000},
            timeout=5,  # 5 秒超时
        )
```

### Step 2: 运行确认失败

```bash
python -m pytest tests/test_docker_executor.py -v -m integration
```

预期：`ModuleNotFoundError: No module named 'bitget_bot.sandbox.docker_executor'`

### Step 3: 实现 Docker 执行器

```python
# bitget_bot/sandbox/docker_executor.py
"""
Docker 沙箱执行器 —— 第二道（主要）安全边界。

每次回测请求都 spin up 一个临时容器，通过 stdin/stdout 通信。
容器配置：无网络、无文件写入、内存 512MB、CPU 1 核、超时 120s。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import docker
from docker.errors import ContainerError, ImageNotFound

from bitget_bot.sandbox.ast_validator import validate_strategy_code

log = logging.getLogger(__name__)

SANDBOX_IMAGE = "strategy-sandbox:latest"

# Docker 安全配置（不可在容器内覆盖）
_SECURITY_OPTS = [
    "no-new-privileges:true",  # 禁止提权
]
_CAP_DROP = ["ALL"]            # 丢弃所有 Linux capabilities
_READ_ONLY = True              # 根文件系统只读
_NETWORK_MODE = "none"         # 完全断网


class SandboxError(Exception):
    """沙箱执行失败（包含代码检查失败和运行时错误）。"""


def run_strategy_in_sandbox(
    strategy_code: str,
    ohlcv: list,
    params: dict,
    timeout: int = 120,
) -> dict[str, Any]:
    """
    在 Docker 沙箱中运行策略回测。

    Args:
        strategy_code: 用户策略 Python 代码
        ohlcv: [[timestamp_ms, open, high, low, close, volume], ...]
        params: 回测参数字典
        timeout: 最长等待秒数

    Returns:
        结果字典（包含 summary, equity_curve, trades）

    Raises:
        SandboxError: 代码检查失败或运行时错误
    """
    # ── Layer 1: AST 静态检查 ──────────────────────────────────
    errors = validate_strategy_code(strategy_code)
    if errors:
        raise SandboxError(
            "代码安全检查未通过:\n" + "\n".join(f"  • {e}" for e in errors)
        )

    # ── Layer 2: Docker 容器执行 ───────────────────────────────
    payload = json.dumps({
        "strategy_code": strategy_code,
        "ohlcv": ohlcv,
        "params": params,
    }).encode()

    client = docker.from_env()

    try:
        client.images.get(SANDBOX_IMAGE)
    except ImageNotFound:
        raise SandboxError(
            f"沙箱镜像 '{SANDBOX_IMAGE}' 不存在，请先运行: "
            f"docker build -f sandbox/Dockerfile.sandbox -t {SANDBOX_IMAGE} sandbox/"
        )

    try:
        container = client.containers.run(
            image=SANDBOX_IMAGE,
            stdin_open=True,
            detach=True,
            # 安全配置
            network_mode=_NETWORK_MODE,
            security_opt=_SECURITY_OPTS,
            cap_drop=_CAP_DROP,
            read_only=_READ_ONLY,
            # 资源限制
            mem_limit="512m",
            memswap_limit="512m",   # 禁用 swap
            nano_cpus=1_000_000_000,  # 1 个 CPU 核
            pids_limit=64,          # 限制进程数（防 fork bomb）
            # 临时文件系统（read_only=True 时需要）
            tmpfs={"/tmp": "size=64m,noexec,nodev"},
        )

        # 写入 stdin
        sock = container.attach_socket(params={"stdin": 1, "stream": 1, "stdout": 0})
        sock._sock.sendall(payload)
        sock._sock.close()

        # 等待结束，设置超时
        try:
            exit_code = container.wait(timeout=timeout)["StatusCode"]
        except Exception:
            container.kill()
            container.remove(force=True)
            raise SandboxError(f"回测执行超时（>{timeout}s），已强制终止容器")

        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
        container.remove(force=True)

    except ContainerError as e:
        raise SandboxError(f"容器启动失败: {e}")

    log.debug("Sandbox exit=%d stderr=%s", exit_code, stderr[:200] if stderr else "")

    if exit_code != 0:
        raise SandboxError(f"策略执行错误: {stderr[:500]}")

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        raise SandboxError(f"沙箱输出格式错误: {stdout[:200]}")

    if not result.get("success"):
        raise SandboxError(result.get("error", "未知错误"))

    return result
```

### Step 4: 运行集成测试

```bash
# 确保 strategy-sandbox 已构建（Task 1 Step 3）
python -m pytest tests/test_docker_executor.py -v -m integration
```

预期：3 个测试全部 PASS（test_timeout 需要约 5s）

### Step 5: Commit

```bash
git add bitget_bot/sandbox/docker_executor.py tests/test_docker_executor.py
git commit -m "feat: add Docker sandbox executor for strategy backtesting"
```

---

## Task 4: 策略 API 路由（DeepSeek + 沙箱接口）

**Files:**
- Create: `bitget_bot/strategy_router.py`
- Modify: `bitget_bot/api.py` (添加 router 注册 + 新的 backtest 端点)
- Modify: `requirements.txt` (添加 `docker`, `openai`, `httpx`)

### Step 1: 安装新依赖

```bash
cd /Users/max/Developer/Bitget
pip install docker openai httpx
echo -e "\n# Strategy Studio\ndocker>=7.0.0\nopenai>=1.0.0\nhttpx>=0.27.0" >> requirements.txt
```

### Step 2: 创建策略路由

```python
# bitget_bot/strategy_router.py
"""
Strategy Studio API 路由。

POST /api/strategy/generate  — 用 DeepSeek 把 Markdown 描述转为 Python 策略代码
POST /api/strategy/backtest  — 在 Docker 沙箱中运行策略回测（异步任务）
GET  /api/strategy/backtest/{job_id}  — 轮询回测结果
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import ccxt
from fastapi import APIRouter, HTTPException
from openai import OpenAI
from pydantic import BaseModel

from bitget_bot.sandbox.docker_executor import run_strategy_in_sandbox, SandboxError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/strategy", tags=["strategy-studio"])

# 内存中存储沙箱回测任务结果（重启清除）
_sandbox_jobs: dict[str, dict] = {}

# ── DeepSeek 系统提示 ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是一个专业的量化交易策略代码生成器。

用户会用自然语言（Markdown 格式）描述他们的交易策略，你需要将其翻译成符合以下接口规范的 Python 代码。

## 必须实现的接口

```python
import numpy as np
import pandas as pd

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    \"\"\"
    输入: OHLCV DataFrame，列名: timestamp(ms), open, high, low, close, volume
    输出: 同一 DataFrame，添加了策略所需的指标列
    \"\"\"
    out = df.copy()
    # 在这里添加指标计算
    return out

def get_signal(df: pd.DataFrame, i: int, params: dict) -> dict:
    \"\"\"
    输入:
      df: 包含指标的 DataFrame
      i: 当前 bar 索引（0 开始）
      params: 参数字典（包含 squeeze_threshold 等）
    输出: 必须包含以下 4 个布尔键的字典
    \"\"\"
    return {
        'long_entry':  bool,   # True = 在下一根 bar 开多
        'short_entry': bool,   # True = 在下一根 bar 开空
        'close_long':  bool,   # True = 在下一根 bar 平多
        'close_short': bool,   # True = 在下一根 bar 平空
    }
```

## 严格规则

1. **只能导入以下模块**: numpy (as np), pandas (as pd), math, dataclasses, typing, collections, functools, itertools, datetime
2. **禁止**: os, sys, subprocess, socket, requests, ccxt, exec, eval, open, 任何文件 IO
3. 必须实现 `add_indicators` 和 `get_signal` 两个函数，函数名不可更改
4. `get_signal` 中访问历史数据用 `df['column'].iloc[i-1]` 等方式
5. 在 `get_signal` 开头做边界检查（如 `if i < 20: return {四个 False}`）
6. 代码必须可直接运行，不含 TODO 或占位符

## 输出格式

只输出 Python 代码，不含任何 Markdown 代码块标记（不要 ```python），不含解释说明。
代码以 `import` 语句开头。
"""


# ── Pydantic 模型 ─────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    markdown: str                    # 用户的 Markdown 策略描述
    model: str = "deepseek-chat"     # DeepSeek 模型名称


class SandboxBacktestRequest(BaseModel):
    strategy_code: str               # Python 策略代码
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "15m"
    days: int = 90
    initial_equity: float = 10_000.0
    leverage: int = 5
    margin_pct: float = 100.0
    fee_rate: float = 0.0005


# ── 端点实现 ──────────────────────────────────────────────────────────────────

@router.post("/generate")
async def generate_strategy(req: GenerateRequest):
    """
    将 Markdown 策略描述翻译为 Python 代码。
    使用 DeepSeek API（OpenAI 兼容）。
    需要环境变量: DEEPSEEK_API_KEY
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="未配置 DEEPSEEK_API_KEY 环境变量，请在 .env 文件中添加"
        )

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com/v1",
    )

    try:
        response = client.chat.completions.create(
            model=req.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": req.markdown},
            ],
            temperature=0.1,   # 代码生成用低温度，确保一致性
            max_tokens=4096,
        )
        code = response.choices[0].message.content.strip()

        # 清理 AI 可能添加的 Markdown 代码块标记
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        return {"code": code, "model": req.model}

    except Exception as e:
        log.exception("DeepSeek API 调用失败")
        raise HTTPException(status_code=502, detail=f"AI 代码生成失败: {str(e)}")


def _fetch_ohlcv(symbol: str, timeframe: str, days: int) -> list:
    """从 Bitget 获取 OHLCV 数据（在主进程中获取，不在沙箱中）。"""
    ex = ccxt.bitget({"options": {"defaultType": "swap"}, "enableRateLimit": True})
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    tf_ms = int(ex.parse_timeframe(timeframe) * 1000)
    now_ms = ex.milliseconds()

    all_rows = []
    cur = since_ms
    while cur < now_ms:
        rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cur, limit=200)
        if not rows: break
        all_rows.extend(rows)
        last_ts = rows[-1][0]
        next_cur = last_ts + tf_ms
        if next_cur <= cur: break
        cur = next_cur

    # 去重、排序、去掉最后未完成的 bar
    seen = set()
    clean = []
    for r in sorted(all_rows, key=lambda x: x[0]):
        if r[0] not in seen and r[0] + tf_ms <= now_ms:
            seen.add(r[0])
            clean.append(r)
    return clean


@router.post("/backtest")
def start_sandbox_backtest(req: SandboxBacktestRequest):
    """
    触发沙箱回测任务（异步，立即返回 job_id）。
    实际执行在后台线程中进行。
    """
    job_id = f"sb_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    _sandbox_jobs[job_id] = {"status": "running", "job_id": job_id}

    def _run():
        try:
            log.info("沙箱回测开始: job=%s symbol=%s days=%d", job_id, req.symbol, req.days)

            # 主进程获取 OHLCV 数据
            ohlcv = _fetch_ohlcv(req.symbol, req.timeframe, req.days)
            if len(ohlcv) < 30:
                _sandbox_jobs[job_id] = {
                    "status": "error",
                    "job_id": job_id,
                    "error": f"数据不足: 只有 {len(ohlcv)} 根 K 线，需要至少 30 根",
                }
                return

            # 提交到 Docker 沙箱
            result = run_strategy_in_sandbox(
                strategy_code=req.strategy_code,
                ohlcv=ohlcv,
                params={
                    "initial_equity": req.initial_equity,
                    "leverage":       req.leverage,
                    "margin_pct":     req.margin_pct,
                    "fee_rate":       req.fee_rate,
                },
                timeout=120,
            )

            _sandbox_jobs[job_id] = {
                "status": "done",
                "job_id": job_id,
                **result,
            }
            log.info("沙箱回测完成: job=%s trades=%d", job_id,
                     result.get("summary", {}).get("total_trades", 0))

        except SandboxError as e:
            log.warning("沙箱回测被拒绝: job=%s error=%s", job_id, e)
            _sandbox_jobs[job_id] = {
                "status": "error",
                "job_id": job_id,
                "error": str(e),
            }
        except Exception as e:
            log.exception("沙箱回测异常: job=%s", job_id)
            _sandbox_jobs[job_id] = {
                "status": "error",
                "job_id": job_id,
                "error": f"内部错误: {str(e)}",
            }

    threading.Thread(target=_run, daemon=True, name=f"sandbox-{job_id}").start()
    return {"job_id": job_id, "status": "running"}


@router.get("/backtest/{job_id}")
def get_sandbox_backtest(job_id: str):
    """轮询沙箱回测任务结果。"""
    job = _sandbox_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    return job
```

### Step 3: 注册路由到 api.py

在 `bitget_bot/api.py` 中找到 `from bitget_bot.runner import start_loop` 这行之后，添加：

```python
from bitget_bot.strategy_router import router as strategy_router
```

在 `app = FastAPI(...)` 定义之后，添加：

```python
app.include_router(strategy_router)
```

### Step 4: 在 .env 文件中添加 DeepSeek key

```bash
# .env 文件末尾添加（替换为你的实际 key）
echo "DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx" >> .env
```

### Step 5: 快速验证 API 启动

```bash
cd /Users/max/Developer/Bitget
uvicorn bitget_bot.api:app --host 0.0.0.0 --port 8080 --reload
# 另一个终端：
curl http://localhost:8080/api/strategy/generate \
  -H "Content-Type: application/json" \
  -d '{"markdown": "## 策略\n当收盘价上穿20日均线时做多，下穿时做空。"}'
```

预期：返回 Python 代码字符串

### Step 6: Commit

```bash
git add bitget_bot/strategy_router.py bitget_bot/api.py requirements.txt
git commit -m "feat: add strategy generation (DeepSeek) and sandbox backtest API"
```

---

## Task 5: 更新 Docker 基础设施

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `sandbox/Dockerfile.sandbox`（添加到主 Dockerfile 构建流）

### Step 1: 更新主 Dockerfile（添加 Docker CLI）

在 `FROM python:3.11-slim` 之后，添加 Docker CLI 安装：

```dockerfile
# 在 Stage 2 中安装 Docker CLI（供主进程调用 docker run 沙箱）
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io \
    && rm -rf /var/lib/apt/lists/*
```

### Step 2: 更新 docker-compose.yml（挂载 Docker socket）

```yaml
version: "3.9"

services:
  bitget-bot:
    build: .
    container_name: bitget-bot
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
      # 挂载 Docker socket，使主容器可以启动兄弟容器（沙箱）
      - /var/run/docker.sock:/var/run/docker.sock
    env_file:
      - .env
    restart: unless-stopped
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    # 主容器需要 Docker 组权限访问 socket
    group_add:
      - "docker"
```

> **安全说明：** 挂载 Docker socket 赋予了容器创建新容器的能力，本质上是主机级权限。在本项目中这是可接受的（自托管服务器），但不适合多租户 SaaS 产品。商业化时需改为独立的 Docker-in-Docker 服务或专用容器编排服务。

### Step 3: 预构建沙箱镜像到主构建流程

在主 `Dockerfile` 末尾增加沙箱镜像的说明注释，并添加 `Makefile`：

```makefile
# Makefile
.PHONY: build sandbox dev

sandbox:
	docker build -f sandbox/Dockerfile.sandbox -t strategy-sandbox:latest sandbox/

build: sandbox
	docker build -t bitget-bot:latest .

dev: sandbox
	uvicorn bitget_bot.api:app --host 0.0.0.0 --port 8080 --reload
```

### Step 4: 验证

```bash
make sandbox  # 构建沙箱镜像
docker images | grep strategy-sandbox
# 预期: strategy-sandbox   latest   <image_id>   ...
```

### Step 5: Commit

```bash
git add Dockerfile docker-compose.yml Makefile
git commit -m "infra: add Docker socket mount and sandbox image build pipeline"
```

---

## Task 6: 前端 — 安装 Monaco Editor 并添加导航

**Files:**
- Modify: `frontend/package.json`（通过 npm install）
- Modify: `frontend/src/App.jsx`（添加 Strategy Studio 路由和导航）
- Create: `frontend/src/components/NavTabs.jsx`

### Step 1: 安装 Monaco Editor

```bash
cd /Users/max/Developer/Bitget/frontend
npm install @monaco-editor/react
```

### Step 2: 更新 App.jsx — 添加标签导航

完整替换 `frontend/src/App.jsx`：

```jsx
import { Component, useState } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import StatusBar from './components/StatusBar'
import CandleChart from './components/CandleChart'
import EquityChart from './components/EquityChart'
import StatsPanel from './components/StatsPanel'
import TradesTable from './components/TradesTable'
import BacktestPanel from './components/BacktestPanel'
import StrategyStudio from './components/StrategyStudio'

class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) { return { error } }
  render() {
    if (this.state.error) {
      return (
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-4 text-red-400 text-sm">
          <div className="font-bold mb-1">组件错误</div>
          <div className="font-mono text-xs">{String(this.state.error)}</div>
        </div>
      )
    }
    return this.props.children
  }
}

const TABS = [
  { id: 'dashboard', label: '📊 监控面板' },
  { id: 'studio',    label: '✦ 策略工作台' },
]

function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  useWebSocket()

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      {/* 顶部导航 */}
      <div className="border-b border-gray-800 bg-gray-950 sticky top-0 z-50">
        <div className="max-w-screen-2xl mx-auto flex items-center gap-0 px-4">
          <span className="text-gray-400 text-sm font-mono mr-6 py-3">Bitget Bot</span>
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-5 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.id
                  ? 'border-blue-500 text-blue-400'
                  : 'border-transparent text-gray-400 hover:text-gray-200'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Dashboard 标签 */}
      {activeTab === 'dashboard' && (
        <>
          <ErrorBoundary><StatusBar /></ErrorBoundary>
          <div className="max-w-screen-2xl mx-auto p-4 space-y-4">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <div className="lg:col-span-1">
                <ErrorBoundary><EquityChart /></ErrorBoundary>
              </div>
              <div className="lg:col-span-2">
                <ErrorBoundary><CandleChart /></ErrorBoundary>
              </div>
            </div>
            <ErrorBoundary><StatsPanel /></ErrorBoundary>
            <ErrorBoundary><TradesTable /></ErrorBoundary>
            <ErrorBoundary><BacktestPanel /></ErrorBoundary>
          </div>
        </>
      )}

      {/* Strategy Studio 标签 */}
      {activeTab === 'studio' && (
        <ErrorBoundary>
          <StrategyStudio />
        </ErrorBoundary>
      )}
    </div>
  )
}

export default App
```

### Step 3: 确认前端可以启动

```bash
cd /Users/max/Developer/Bitget/frontend
npm run dev
# 访问 http://localhost:5173
# 预期: 顶部出现 "📊 监控面板" 和 "✦ 策略工作台" 两个标签
```

### Step 4: Commit

```bash
git add frontend/src/App.jsx frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): add tab navigation and Monaco Editor dependency"
```

---

## Task 7: 前端 — Markdown 编辑器组件

**Files:**
- Create: `frontend/src/components/studio/MarkdownEditor.jsx`

```jsx
// frontend/src/components/studio/MarkdownEditor.jsx
import Editor from '@monaco-editor/react'

const DEFAULT_MARKDOWN = `# 我的交易策略

## 策略名称
均线金叉死叉策略

## 适用市场
BTC/USDT 永续合约，15 分钟 K 线

## 指标

- **SMA20**: 20 周期简单移动平均线
- **SMA60**: 60 周期简单移动平均线

## 入场条件

### 做多信号
- SMA20 向上穿越 SMA60（金叉）
- 当前收盘价高于 SMA60

### 做空信号
- SMA20 向下穿越 SMA60（死叉）
- 当前收盘价低于 SMA60

## 出场条件

### 多仓出场
- SMA20 再次下穿 SMA60（死叉）
- 或收盘价跌破 SMA60 的 2%

### 空仓出场
- SMA20 再次上穿 SMA60（金叉）
- 或收盘价突破 SMA60 的 2%

## 风险控制

- 杠杆: 5 倍
- 每次使用 100% 仓位保证金
- 手续费率: 0.05% 每边
`

export default function MarkdownEditor({ value, onChange }) {
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 bg-gray-900 border-b border-gray-700">
        <span className="text-xs text-gray-400 font-mono">📝 策略文档 (Markdown)</span>
        <span className="text-xs text-gray-600">描述你的策略，AI 将自动翻译为代码</span>
      </div>
      <div className="flex-1 min-h-0">
        <Editor
          height="100%"
          defaultLanguage="markdown"
          value={value}
          onChange={onChange}
          theme="vs-dark"
          options={{
            fontSize: 13,
            lineHeight: 22,
            wordWrap: 'on',
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            renderLineHighlight: 'none',
            overviewRulerLanes: 0,
            hideCursorInOverviewRuler: true,
            padding: { top: 12, bottom: 12 },
            fontFamily: '"JetBrains Mono", "Fira Code", monospace',
          }}
        />
      </div>
    </div>
  )
}

export { DEFAULT_MARKDOWN }
```

---

## Task 8: 前端 — Python 代码编辑器组件

**Files:**
- Create: `frontend/src/components/studio/CodeEditor.jsx`

```jsx
// frontend/src/components/studio/CodeEditor.jsx
import Editor from '@monaco-editor/react'

const PLACEHOLDER_CODE = `# 点击左侧的「▶ 生成代码」按钮，AI 将根据你的策略描述自动生成 Python 代码

import numpy as np
import pandas as pd

def add_indicators(df):
    out = df.copy()
    # 在这里添加指标计算
    return out

def get_signal(df, i, params):
    return {
        'long_entry':  False,
        'short_entry': False,
        'close_long':  False,
        'close_short': False,
    }
`

export default function CodeEditor({ value, onChange, generating }) {
  const displayValue = value || PLACEHOLDER_CODE

  return (
    <div className="flex flex-col h-full relative">
      <div className="flex items-center justify-between px-3 py-2 bg-gray-900 border-b border-gray-700">
        <span className="text-xs text-gray-400 font-mono">🐍 策略代码 (Python)</span>
        {generating ? (
          <span className="text-xs text-blue-400 animate-pulse">AI 生成中...</span>
        ) : value ? (
          <span className="text-xs text-green-500">✓ 已生成，可手动修改</span>
        ) : (
          <span className="text-xs text-gray-600">等待生成</span>
        )}
      </div>
      <div className="flex-1 min-h-0">
        <Editor
          height="100%"
          defaultLanguage="python"
          value={displayValue}
          onChange={onChange}
          theme="vs-dark"
          options={{
            fontSize: 13,
            lineHeight: 20,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            padding: { top: 12, bottom: 12 },
            fontFamily: '"JetBrains Mono", "Fira Code", monospace',
            readOnly: generating,
          }}
        />
      </div>

      {/* 生成中遮罩 */}
      {generating && (
        <div className="absolute inset-0 bg-gray-950/60 flex items-center justify-center pointer-events-none">
          <div className="bg-gray-900 border border-blue-800 rounded-lg px-6 py-4 text-blue-400 text-sm">
            <div className="flex items-center gap-2">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
              DeepSeek 正在分析策略文档...
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

---

## Task 9: 前端 — 回测结果面板

**Files:**
- Create: `frontend/src/components/studio/BacktestResults.jsx`

```jsx
// frontend/src/components/studio/BacktestResults.jsx
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import dayjs from 'dayjs'

function StatCard({ label, value, sub, color = 'text-white' }) {
  return (
    <div className="bg-gray-900 rounded-lg p-3 border border-gray-800">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className={`text-lg font-mono font-bold ${color}`}>{value}</div>
      {sub && <div className="text-xs text-gray-600 mt-0.5">{sub}</div>}
    </div>
  )
}

export default function BacktestResults({ result, loading, error }) {
  if (loading) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500 text-sm">
        <svg className="animate-spin h-5 w-5 mr-2" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
        </svg>
        在 Docker 沙箱中运行回测...
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-red-900/20 border border-red-800 rounded-lg p-4 m-4 text-red-400 text-sm">
        <div className="font-bold mb-1">回测失败</div>
        <pre className="font-mono text-xs whitespace-pre-wrap">{error}</pre>
      </div>
    )
  }

  if (!result) return null

  const { summary, equity_curve, trades } = result
  const pnlColor = summary.total_pnl_usdt >= 0 ? 'text-green-400' : 'text-red-400'
  const retColor = summary.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'

  const chartData = equity_curve.map(p => ({
    ts:     dayjs(p.ts).valueOf(),
    equity: p.equity,
  }))

  return (
    <div className="space-y-4 p-4">
      {/* 统计摘要 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
        <StatCard label="总收益"
          value={`${summary.total_return_pct >= 0 ? '+' : ''}${summary.total_return_pct}%`}
          sub={`$${summary.total_pnl_usdt >= 0 ? '+' : ''}${summary.total_pnl_usdt}`}
          color={retColor}
        />
        <StatCard label="最终权益"
          value={`$${summary.final_equity.toLocaleString()}`}
          sub={`初始 $${summary.initial_equity.toLocaleString()}`}
        />
        <StatCard label="胜率"
          value={`${summary.win_rate_pct}%`}
          sub={`${summary.wins}胜 / ${summary.losses}负`}
          color={summary.win_rate_pct >= 50 ? 'text-green-400' : 'text-red-400'}
        />
        <StatCard label="盈亏比"
          value={summary.profit_factor ?? '∞'}
          sub="盈利总额 / 亏损总额"
          color={summary.profit_factor >= 1.5 ? 'text-green-400' : 'text-yellow-400'}
        />
        <StatCard label="最大回撤"
          value={`${summary.max_drawdown_pct}%`}
          color="text-red-400"
        />
        <StatCard label="总交易次数"
          value={summary.total_trades}
          sub={`多 ${summary.long_trades} / 空 ${summary.short_trades}`}
        />
        <StatCard label="手续费"
          value={`$${summary.total_fee_usdt}`}
          color="text-yellow-600"
        />
        <StatCard label="回测区间"
          value={dayjs(summary.date_from).format('MM/DD')}
          sub={`→ ${dayjs(summary.date_to).format('MM/DD')}`}
        />
      </div>

      {/* 权益曲线 */}
      <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
        <div className="text-xs text-gray-500 mb-3">权益曲线</div>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={chartData}>
            <XAxis
              dataKey="ts"
              type="number"
              domain={['dataMin', 'dataMax']}
              tickFormatter={ts => dayjs(ts).format('MM/DD')}
              tick={{ fontSize: 11, fill: '#6b7280' }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={['auto', 'auto']}
              tick={{ fontSize: 11, fill: '#6b7280' }}
              axisLine={false}
              tickLine={false}
              tickFormatter={v => `$${(v/1000).toFixed(1)}k`}
              width={55}
            />
            <Tooltip
              formatter={v => [`$${v.toFixed(2)}`, '权益']}
              labelFormatter={ts => dayjs(ts).format('YYYY-MM-DD HH:mm')}
              contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 12 }}
            />
            <ReferenceLine
              y={summary.initial_equity}
              stroke="#4b5563"
              strokeDasharray="4 4"
            />
            <Line
              type="monotone"
              dataKey="equity"
              stroke={summary.total_return_pct >= 0 ? '#22c55e' : '#ef4444'}
              dot={false}
              strokeWidth={2}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* 交易列表 */}
      <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
        <div className="px-4 py-2 border-b border-gray-800 text-xs text-gray-500">
          交易记录（最近 50 条）
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-600 border-b border-gray-800">
                <th className="px-3 py-2 text-left">#</th>
                <th className="px-3 py-2 text-left">方向</th>
                <th className="px-3 py-2 text-left">开仓时间</th>
                <th className="px-3 py-2 text-right">开仓价</th>
                <th className="px-3 py-2 text-right">平仓价</th>
                <th className="px-3 py-2 text-right">PnL%</th>
                <th className="px-3 py-2 text-right">PnL(USDT)</th>
              </tr>
            </thead>
            <tbody>
              {trades.slice(-50).map((t, idx) => {
                const isWin = t.pnl_usdt > 0
                return (
                  <tr key={idx} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-3 py-1.5 text-gray-600">{trades.length - 50 + idx + 1}</td>
                    <td className="px-3 py-1.5">
                      <span className={t.direction === 'long' ? 'text-green-400' : 'text-red-400'}>
                        {t.direction === 'long' ? '多' : '空'}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-gray-400">
                      {dayjs(t.entry_time).format('MM-DD HH:mm')}
                    </td>
                    <td className="px-3 py-1.5 text-right text-gray-300">{t.entry_price?.toFixed(2)}</td>
                    <td className="px-3 py-1.5 text-right text-gray-300">{t.exit_price?.toFixed(2)}</td>
                    <td className={`px-3 py-1.5 text-right ${isWin ? 'text-green-400' : 'text-red-400'}`}>
                      {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct?.toFixed(2)}%
                    </td>
                    <td className={`px-3 py-1.5 text-right font-bold ${isWin ? 'text-green-400' : 'text-red-400'}`}>
                      {t.pnl_usdt >= 0 ? '+' : ''}${t.pnl_usdt?.toFixed(2)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
```

---

## Task 10: 前端 — Strategy Studio 主页面

**Files:**
- Create: `frontend/src/components/StrategyStudio.jsx`

```jsx
// frontend/src/components/StrategyStudio.jsx
import { useState, useRef, useCallback } from 'react'
import MarkdownEditor, { DEFAULT_MARKDOWN } from './studio/MarkdownEditor'
import CodeEditor from './studio/CodeEditor'
import BacktestResults from './studio/BacktestResults'

const API = '/api/strategy'

// 回测参数默认值
const DEFAULT_PARAMS = {
  symbol: 'BTC/USDT:USDT',
  timeframe: '15m',
  days: 90,
  initial_equity: 10000,
  leverage: 5,
  margin_pct: 100,
  fee_rate: 0.0005,
}

export default function StrategyStudio() {
  const [markdown, setMarkdown]           = useState(DEFAULT_MARKDOWN)
  const [code, setCode]                   = useState('')
  const [generating, setGenerating]       = useState(false)
  const [backtestLoading, setBtLoading]   = useState(false)
  const [backtestResult, setBtResult]     = useState(null)
  const [backtestError, setBtError]       = useState(null)
  const [params, setParams]               = useState(DEFAULT_PARAMS)
  const [generationError, setGenError]    = useState(null)
  const pollRef                           = useRef(null)

  // ── 生成代码 ────────────────────────────────────────────────
  const handleGenerate = useCallback(async () => {
    setGenerating(true)
    setGenError(null)
    try {
      const res = await fetch(`${API}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ markdown }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '生成失败')
      setCode(data.code)
    } catch (e) {
      setGenError(e.message)
    } finally {
      setGenerating(false)
    }
  }, [markdown])

  // ── 重新生成（清空当前代码后生成）───────────────────────────
  const handleRegenerate = useCallback(() => {
    setCode('')
    handleGenerate()
  }, [handleGenerate])

  // ── 运行回测 ─────────────────────────────────────────────────
  const handleBacktest = useCallback(async () => {
    if (!code || code.trim() === '') {
      setBtError('请先生成或编写策略代码')
      return
    }
    setBtLoading(true)
    setBtResult(null)
    setBtError(null)

    try {
      // 提交任务
      const res = await fetch(`${API}/backtest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy_code: code, ...params }),
      })
      const { job_id } = await res.json()

      // 轮询结果
      const poll = async () => {
        try {
          const r = await fetch(`${API}/backtest/${job_id}`)
          const job = await r.json()
          if (job.status === 'running') {
            pollRef.current = setTimeout(poll, 2000)
          } else if (job.status === 'done') {
            setBtResult(job)
            setBtLoading(false)
            // 滚动到结果
            document.getElementById('backtest-results')?.scrollIntoView({ behavior: 'smooth' })
          } else {
            setBtError(job.error || '回测失败')
            setBtLoading(false)
          }
        } catch {
          setBtError('轮询回测结果失败')
          setBtLoading(false)
        }
      }
      pollRef.current = setTimeout(poll, 1500)

    } catch (e) {
      setBtError(e.message)
      setBtLoading(false)
    }
  }, [code, params])

  return (
    <div className="flex flex-col h-[calc(100vh-48px)]">
      {/* ── 双栏编辑区 ─────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0">
        {/* 左侧: Markdown 编辑器 */}
        <div className="w-1/2 flex flex-col border-r border-gray-800">
          <div className="flex-1 min-h-0">
            <MarkdownEditor value={markdown} onChange={setMarkdown} />
          </div>

          {/* 左侧底部操作按钮 */}
          <div className="flex items-center gap-2 px-3 py-2 bg-gray-900 border-t border-gray-800">
            <button
              onClick={handleGenerate}
              disabled={generating || !markdown.trim()}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-blue-600 hover:bg-blue-500
                         disabled:opacity-50 disabled:cursor-not-allowed
                         text-white text-sm rounded-md font-medium transition-colors"
            >
              {generating ? (
                <>
                  <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                  </svg>
                  生成中...
                </>
              ) : '▶ 生成代码'}
            </button>
            {code && (
              <button
                onClick={handleRegenerate}
                disabled={generating}
                className="px-3 py-1.5 text-gray-400 hover:text-gray-200 text-sm
                           border border-gray-700 hover:border-gray-500 rounded-md transition-colors"
              >
                重新生成
              </button>
            )}
            {generationError && (
              <span className="text-red-400 text-xs ml-2">{generationError}</span>
            )}
          </div>
        </div>

        {/* 右侧: Python 代码编辑器 */}
        <div className="w-1/2 flex flex-col">
          <div className="flex-1 min-h-0">
            <CodeEditor value={code} onChange={setCode} generating={generating} />
          </div>

          {/* 右侧底部：复制代码 */}
          {code && (
            <div className="flex items-center gap-2 px-3 py-2 bg-gray-900 border-t border-gray-800">
              <button
                onClick={() => navigator.clipboard.writeText(code)}
                className="px-3 py-1.5 text-gray-400 hover:text-gray-200 text-xs
                           border border-gray-700 hover:border-gray-500 rounded-md transition-colors"
              >
                📋 复制代码
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── 回测参数栏 ─────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-4 py-2.5 bg-gray-900 border-t border-gray-700 flex-wrap">
        <span className="text-xs text-gray-500 font-mono">回测参数</span>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          交易对
          <input
            value={params.symbol}
            onChange={e => setParams(p => ({ ...p, symbol: e.target.value }))}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-36 text-gray-200 text-xs"
          />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          天数
          <input
            type="number" min="7" max="365"
            value={params.days}
            onChange={e => setParams(p => ({ ...p, days: +e.target.value }))}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-16 text-gray-200 text-xs"
          />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          杠杆
          <input
            type="number" min="1" max="50"
            value={params.leverage}
            onChange={e => setParams(p => ({ ...p, leverage: +e.target.value }))}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-14 text-gray-200 text-xs"
          />
          <span className="text-gray-600">x</span>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          初始资金
          <input
            type="number" min="100"
            value={params.initial_equity}
            onChange={e => setParams(p => ({ ...p, initial_equity: +e.target.value }))}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-20 text-gray-200 text-xs"
          />
          <span className="text-gray-600">USDT</span>
        </label>
        <button
          onClick={handleBacktest}
          disabled={backtestLoading || !code}
          className="ml-auto flex items-center gap-1.5 px-4 py-1.5 bg-green-700 hover:bg-green-600
                     disabled:opacity-50 disabled:cursor-not-allowed
                     text-white text-sm rounded-md font-medium transition-colors"
        >
          {backtestLoading ? (
            <>
              <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
              运行中...
            </>
          ) : '▶ 运行回测'}
        </button>
      </div>

      {/* ── 回测结果 ───────────────────────────────────────── */}
      <div id="backtest-results" className="overflow-y-auto max-h-[45vh] border-t border-gray-800">
        <BacktestResults
          result={backtestResult}
          loading={backtestLoading}
          error={backtestError}
        />
      </div>
    </div>
  )
}
```

### Step 1: 确认前端编译无报错

```bash
cd /Users/max/Developer/Bitget/frontend
npm run dev
# 访问 http://localhost:5173
# 点击 "✦ 策略工作台" 标签
# 预期: 双栏布局，左侧 Markdown 编辑器加载默认策略文档，右侧代码编辑器空白
```

### Step 2: Commit

```bash
git add frontend/src/components/
git commit -m "feat(frontend): add Strategy Studio with Monaco editors and backtest UI"
```

---

## Task 11: 端对端集成测试

### 功能验证清单

**1. AI 代码生成测试**
```bash
# 确保后端在运行（含 DEEPSEEK_API_KEY）
curl -X POST http://localhost:8080/api/strategy/generate \
  -H "Content-Type: application/json" \
  -d '{
    "markdown": "## 布林带策略\n当价格突破布林带上轨时做空，突破下轨时做多。使用20周期，2倍标准差。"
  }'
# 预期: {"code": "import numpy as np\nimport pandas as pd\n...", "model": "deepseek-chat"}
```

**2. AST 安全检查测试（注入攻击被拦截）**
```bash
curl -X POST http://localhost:8080/api/strategy/backtest \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_code": "import os\nos.system(\"cat /etc/passwd\")\ndef add_indicators(df): return df\ndef get_signal(df,i,p): return {}",
    "symbol": "BTC/USDT:USDT",
    "days": 7
  }'
# 预期: {"status": "error", "error": "代码安全检查未通过: 第 1 行: 不允许导入 '\''os'\''..."}
```

**3. 正常回测流程测试**
```bash
# 提交回测
JOB=$(curl -s -X POST http://localhost:8080/api/strategy/backtest \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_code": "import numpy as np\nimport pandas as pd\n\ndef add_indicators(df):\n    out = df.copy()\n    out[\"sma20\"] = out[\"close\"].rolling(20).mean()\n    return out\n\ndef get_signal(df, i, params):\n    if i < 20 or pd.isna(df[\"sma20\"].iloc[i]):\n        return {\"long_entry\": False, \"short_entry\": False, \"close_long\": False, \"close_short\": False}\n    c = df[\"close\"].iloc[i]\n    s = df[\"sma20\"].iloc[i]\n    return {\"long_entry\": c > s, \"short_entry\": c < s, \"close_long\": c < s, \"close_short\": c > s}",
    "symbol": "BTC/USDT:USDT",
    "days": 30
  }' | python3 -c "import sys,json; print(json.load(sys.stdin)['\''job_id'\''])")

echo "Job ID: $JOB"

# 轮询直到完成（约 30-60s）
sleep 30
curl http://localhost:8080/api/strategy/backtest/$JOB | python3 -m json.tool | head -40
# 预期: status=done, summary 包含 total_trades, win_rate_pct 等
```

**4. 前端 UI 测试**
1. 访问 `http://localhost:5173`，点击 "✦ 策略工作台"
2. 左侧默认加载示例 Markdown 策略文档
3. 点击 "▶ 生成代码"，右侧出现 AI 生成的 Python 代码（约 5-15 秒）
4. 手动修改右侧代码（将 SMA 周期从 20 改为 30）
5. 调整回测参数（天数改为 60）
6. 点击 "▶ 运行回测"，底部出现加载动画
7. 约 30-90 秒后，底部显示回测结果（统计摘要 + 权益曲线 + 交易列表）
8. 验证"重新生成"按钮将右侧代码替换为新版本

### Step: 最终 Commit

```bash
git add .
git commit -m "feat: complete Strategy Studio with AI code generation, Docker sandbox, and backtest UI"
```

---

## 新增文件清单

```
Bitget/
├── sandbox/
│   ├── Dockerfile.sandbox          # 沙箱 Docker 镜像
│   └── sandbox_runner.py           # 沙箱内回测执行器
├── bitget_bot/
│   └── sandbox/
│       ├── __init__.py
│       ├── ast_validator.py        # AST 白名单静态分析
│       └── docker_executor.py      # Docker SDK 执行器
├── bitget_bot/
│   └── strategy_router.py          # FastAPI 路由（DeepSeek + 沙箱）
├── frontend/src/components/
│   ├── StrategyStudio.jsx          # 主页面
│   └── studio/
│       ├── MarkdownEditor.jsx      # 左侧编辑器
│       ├── CodeEditor.jsx          # 右侧编辑器
│       └── BacktestResults.jsx     # 回测结果面板
├── tests/
│   ├── test_ast_validator.py
│   └── test_docker_executor.py
└── Makefile                        # 构建快捷命令
```

## 修改文件清单

```
├── bitget_bot/api.py               # +注册 strategy_router
├── frontend/src/App.jsx            # +标签导航 + StrategyStudio 路由
├── docker-compose.yml              # +Docker socket 挂载
├── Dockerfile                      # +Docker CLI 安装
├── requirements.txt                # +docker, openai, httpx
└── .env                            # +DEEPSEEK_API_KEY
```

---

## 依赖版本锁定

```
# 新增到 requirements.txt
docker>=7.0.0          # Docker Python SDK
openai>=1.0.0          # DeepSeek API (OpenAI 兼容)
httpx>=0.27.0          # openai 的 HTTP 依赖

# 新增到 frontend/package.json (npm install)
@monaco-editor/react   # Monaco 编辑器 React 封装
```

---

## 安全架构总结

| 层级 | 技术 | 防护目标 |
|------|------|---------|
| Layer 1 | AST 白名单分析 | 快速拒绝明显危险代码（os/subprocess/exec 等） |
| Layer 2 | Docker 容器隔离 | 主要安全边界，进程/文件系统/网络完全隔离 |
| Layer 3 | `--network=none` | 沙箱容器无法访问任何网络 |
| Layer 4 | `--memory=512m` | 防止内存耗尽攻击 |
| Layer 5 | `--cpus=1` | 防止 CPU 占满 |
| Layer 6 | `--pids-limit=64` | 防止 fork bomb |
| Layer 7 | `--read-only` | 防止文件系统篡改 |
| Layer 8 | `timeout=120s` | 防止无限循环阻塞 |
| Layer 9 | 非 root 用户运行 | 降低容器逃逸影响 |
| Layer 10 | 环境变量隔离 | 防止 API 密钥泄露到沙箱 |

---

## Task 12: AI 生成代码的 Debug 机制

### 背景：错误分类

AI 生成代码可能出现三类错误，需要不同的检测和修复路径：

```
错误类型          检测时机           来源            修复方式
─────────────────────────────────────────────────────────────
1. 语法错误       生成后立即         ast.parse()     AI 修复 / 手动编辑
2. 接口违规       生成后立即         AST 验证器      AI 修复
   (禁止导入等)
3. 运行时错误     回测执行后         Docker stderr   AI 修复 / 手动编辑
   (NameError,
   IndexError 等)
4. 逻辑错误       回测完成后         0 交易 / 异常   手动分析 / AI 建议
   (无信号等)      人工判断           结果
```

### Debug 闭环架构

```
用户代码（右侧编辑器）
    │
    │ 实时（800ms 防抖）
    ▼
POST /api/strategy/validate
    │
    ├─ 语法错误 ──→ Monaco 红色波浪线（行号精确）
    ├─ 接口违规 ──→ Monaco 黄色警告线
    └─ 接口缺失 ──→ 底部错误面板
    
回测失败时
    │
    ├─ Docker stderr 解析 ──→ 错误面板（含 traceback）
    └─ "🔧 AI 修复" 按钮
            │
            ▼
    POST /api/strategy/fix
    {code, error_message, error_type}
            │
            ▼
    DeepSeek API（修复专用 prompt）
            │
            ▼
    右侧编辑器更新为修复后代码
    可再次点击"运行回测"验证
```

**Files:**
- Create: `bitget_bot/sandbox/code_validator.py`（扩展现有 ast_validator，增加语法错误结构化输出）
- Modify: `bitget_bot/strategy_router.py`（新增 `/validate` 和 `/fix` 端点）
- Create: `frontend/src/components/studio/CodeErrorPanel.jsx`
- Create: `frontend/src/hooks/useCodeValidation.js`
- Modify: `frontend/src/components/studio/CodeEditor.jsx`（集成 Monaco 错误标记）
- Modify: `frontend/src/components/StrategyStudio.jsx`（集成 debug 流程）

### Step 1: 写测试

```python
# tests/test_code_validator.py
from bitget_bot.sandbox.code_validator import validate_code_full

def test_syntax_error_returns_line_number():
    code = "def foo(\n    pass\n"
    result = validate_code_full(code)
    assert result["valid"] is False
    syntax_errs = [e for e in result["errors"] if e["type"] == "syntax"]
    assert len(syntax_errs) == 1
    assert syntax_errs[0]["line"] is not None
    assert syntax_errs[0]["line"] >= 1

def test_security_error_returns_line_number():
    code = "import numpy as np\nimport os\n\ndef add_indicators(df): return df\ndef get_signal(df,i,p): return {}\n"
    result = validate_code_full(code)
    assert result["valid"] is False
    sec_errs = [e for e in result["errors"] if e["type"] == "security"]
    assert any(e["line"] == 2 for e in sec_errs)

def test_missing_interface_functions():
    code = "import numpy as np\n\ndef add_indicators(df): return df\n"
    result = validate_code_full(code)
    assert result["valid"] is False
    iface_errs = [e for e in result["errors"] if e["type"] == "interface"]
    assert any("get_signal" in e["message"] for e in iface_errs)

def test_valid_code_passes():
    code = (
        "import numpy as np\nimport pandas as pd\n\n"
        "def add_indicators(df):\n    return df.copy()\n\n"
        "def get_signal(df, i, params):\n"
        "    return {'long_entry': False, 'short_entry': False,\n"
        "            'close_long': False, 'close_short': False}\n"
    )
    result = validate_code_full(code)
    assert result["valid"] is True
    assert result["errors"] == []

def test_runtime_error_parsing():
    from bitget_bot.sandbox.code_validator import parse_traceback
    tb = (
        'Traceback (most recent call last):\n'
        '  File "<strategy>", line 8, in get_signal\n'
        "NameError: name 'sma20' is not defined\n"
    )
    parsed = parse_traceback(tb)
    assert parsed["line"] == 8
    assert "sma20" in parsed["message"]
    assert parsed["error_type"] == "NameError"
```

### Step 2: 运行确认失败

```bash
python -m pytest tests/test_code_validator.py -v
# 预期: ModuleNotFoundError: No module named 'bitget_bot.sandbox.code_validator'
```

### Step 3: 实现代码验证器（扩展 ast_validator）

```python
# bitget_bot/sandbox/code_validator.py
"""
策略代码完整验证器：结构化错误输出，供前端 Monaco 显示精确位置。
"""
from __future__ import annotations

import ast
import re
from typing import TypedDict, List, Optional

from bitget_bot.sandbox.ast_validator import (
    ALLOWED_IMPORTS, FORBIDDEN_BUILTINS, FORBIDDEN_ATTRS
)


class CodeError(TypedDict):
    type: str          # "syntax" | "security" | "interface" | "style"
    severity: str      # "error" | "warning"
    line: Optional[int]
    col: Optional[int]
    message: str
    end_line: Optional[int]


class ValidationResult(TypedDict):
    valid: bool
    errors: List[CodeError]


class TracebackInfo(TypedDict):
    line: Optional[int]
    error_type: str
    message: str
    full_traceback: str


class _DetailedValidator(ast.NodeVisitor):
    def __init__(self):
        self.errors: List[CodeError] = []
        self._defined_functions: set[str] = set()

    def _err(self, node, msg: str, severity="error", etype="security"):
        self.errors.append(CodeError(
            type=etype,
            severity=severity,
            line=getattr(node, "lineno", None),
            col=getattr(node, "col_offset", None),
            message=msg,
            end_line=getattr(node, "end_lineno", None),
        ))

    def visit_Import(self, node):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top not in ALLOWED_IMPORTS:
                self._err(node, f"不允许导入 '{alias.name}'，仅允许: numpy, pandas, math 等")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        top = (node.module or "").split(".")[0]
        if top not in ALLOWED_IMPORTS:
            self._err(node, f"不允许 'from {node.module} import ...'")
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BUILTINS:
            self._err(node, f"禁止调用 '{node.func.id}()'")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr in FORBIDDEN_ATTRS:
            self._err(node, f"禁止访问 '{node.attr}'（潜在沙箱逃逸路径）", severity="error")
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._defined_functions.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self._err(node, "策略代码不允许使用 async/await（必须是同步代码）", etype="style")
        self.generic_visit(node)


def validate_code_full(source: str) -> ValidationResult:
    """
    完整验证策略代码，返回结构化错误列表（含行号）。
    供后端 /validate 端点和前端 Monaco 标记使用。
    """
    errors: List[CodeError] = []

    # ── 1. 语法检查（最优先）───────────────────────────────
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ValidationResult(
            valid=False,
            errors=[CodeError(
                type="syntax",
                severity="error",
                line=e.lineno,
                col=e.offset,
                message=f"语法错误: {e.msg}",
                end_line=e.lineno,
            )]
        )

    # ── 2. 安全性检查（AST 遍历）──────────────────────────
    validator = _DetailedValidator()
    validator.visit(tree)
    errors.extend(validator.errors)

    # ── 3. 接口完整性检查 ─────────────────────────────────
    if "add_indicators" not in validator._defined_functions:
        errors.append(CodeError(
            type="interface", severity="error", line=None, col=None,
            message="缺少必须的函数 'add_indicators(df)'，该函数用于计算技术指标",
            end_line=None,
        ))
    if "get_signal" not in validator._defined_functions:
        errors.append(CodeError(
            type="interface", severity="error", line=None, col=None,
            message="缺少必须的函数 'get_signal(df, i, params)'，该函数用于生成交易信号",
            end_line=None,
        ))

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def parse_traceback(stderr: str) -> TracebackInfo:
    """
    从 Docker 容器的 stderr 中解析 Python traceback，提取行号和错误类型。
    用于将运行时错误精确定位到代码行。
    """
    # 从 traceback 中提取 <strategy> 文件的行号
    # 格式：  File "<strategy>", line N, in function_name
    strategy_line = None
    pattern = re.compile(r'File "<strategy>",\s+line\s+(\d+)')
    for match in pattern.finditer(stderr):
        strategy_line = int(match.group(1))
    # 取最后一个匹配（最深层调用）

    # 提取错误类型和消息
    # 格式：ErrorType: message
    last_line = stderr.strip().split("\n")[-1] if stderr.strip() else ""
    error_match = re.match(r'^(\w+(?:Error|Exception|Warning)?):\s*(.*)', last_line)
    if error_match:
        error_type = error_match.group(1)
        message = error_match.group(2)
    else:
        error_type = "RuntimeError"
        message = last_line or "未知运行时错误"

    return TracebackInfo(
        line=strategy_line,
        error_type=error_type,
        message=f"{error_type}: {message}",
        full_traceback=stderr,
    )
```

### Step 4: 新增 /validate 和 /fix 端点

在 `bitget_bot/strategy_router.py` 中添加（在现有端点之后）：

```python
# 在 strategy_router.py 末尾追加以下两个端点

from bitget_bot.sandbox.code_validator import validate_code_full

class ValidateRequest(BaseModel):
    code: str

class FixRequest(BaseModel):
    code: str
    error_message: str
    error_type: str = "unknown"   # syntax | runtime | interface | unknown


_FIX_SYSTEM_PROMPT = """你是一个 Python 交易策略调试专家。
用户会给你一段有错误的策略代码和对应的错误信息，你需要修复代码中的错误并返回完整的修复后代码。

策略代码必须遵守以下接口规范：
1. 必须定义 add_indicators(df: pd.DataFrame) -> pd.DataFrame
2. 必须定义 get_signal(df: pd.DataFrame, i: int, params: dict) -> dict
3. get_signal 必须返回包含 long_entry, short_entry, close_long, close_short 四个布尔值的字典
4. 只能导入：numpy (as np), pandas (as pd), math, dataclasses, typing, collections, functools, itertools, datetime
5. 禁止：os, sys, subprocess, socket, exec, eval, open

修复原则：
- 只修复错误，保留用户的策略逻辑不变
- 如果是缺少 NaN 检查导致的 IndexError/ValueError，在 get_signal 开头添加边界检查
- 如果是变量未定义，检查 add_indicators 是否已计算了该指标
- 不要添加注释解释你修改了什么

只输出 Python 代码，不含 Markdown 代码块标记。"""


@router.post("/validate")
def validate_strategy(req: ValidateRequest):
    """
    实时验证策略代码，返回结构化错误列表（含行号）。
    前端用于 Monaco 编辑器的实时错误标记（防抖 800ms 后调用）。
    """
    result = validate_code_full(req.code)
    return result


@router.post("/fix")
async def fix_strategy(req: FixRequest):
    """
    AI 辅助修复策略代码错误。
    接收有错误的代码 + 错误信息，返回修复后的代码。
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="未配置 DEEPSEEK_API_KEY")

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")

    user_message = f"""以下策略代码存在错误，请修复：

错误信息：
{req.error_message}

错误类型：{req.error_type}

有错误的代码：
{req.code}"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": _FIX_SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.05,  # 修复任务用极低温度，确保稳定性
            max_tokens=4096,
        )
        fixed_code = response.choices[0].message.content.strip()

        # 清理可能的 Markdown 代码块包裹
        if fixed_code.startswith("```"):
            lines = fixed_code.split("\n")
            fixed_code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        # 验证修复后的代码是否通过检查
        validation = validate_code_full(fixed_code)

        return {
            "code": fixed_code,
            "validation": validation,
            "fixed": validation["valid"],
        }
    except Exception as e:
        log.exception("AI 修复调用失败")
        raise HTTPException(status_code=502, detail=f"AI 修复失败: {str(e)}")
```

### Step 5: 前端 — 代码验证 Hook

```js
// frontend/src/hooks/useCodeValidation.js
import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * 实时验证策略代码，返回结构化错误列表。
 * 防抖 800ms，避免每次按键都调用 API。
 *
 * 返回的 errors 格式：
 * [{ type, severity, line, col, message }]
 *
 * monacoErrors 格式（用于 editor.setModelMarkers）：
 * [{ startLineNumber, endLineNumber, startColumn, message, severity }]
 */
export function useCodeValidation(code) {
  const [errors, setErrors]       = useState([])
  const [validating, setValidating] = useState(false)
  const timerRef = useRef(null)

  const validate = useCallback(async (src) => {
    if (!src || !src.trim()) {
      setErrors([])
      return
    }
    setValidating(true)
    try {
      const res = await fetch('/api/strategy/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: src }),
      })
      const data = await res.json()
      setErrors(data.errors || [])
    } catch {
      // 验证失败时不展示错误，避免干扰用户
    } finally {
      setValidating(false)
    }
  }, [])

  useEffect(() => {
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => validate(code), 800)
    return () => clearTimeout(timerRef.current)
  }, [code, validate])

  // 转换为 Monaco markers 格式（Monaco severity: 8=Error, 4=Warning）
  const monacoMarkers = errors
    .filter(e => e.line != null)
    .map(e => ({
      startLineNumber: e.line,
      endLineNumber:   e.end_line || e.line,
      startColumn:     (e.col ?? 0) + 1,
      endColumn:       1000,
      message:         e.message,
      severity:        e.severity === 'error' ? 8 : 4,
    }))

  return { errors, validating, monacoMarkers }
}
```

### Step 6: 前端 — 错误面板组件

```jsx
// frontend/src/components/studio/CodeErrorPanel.jsx
/**
 * 展示三种来源的错误：
 * 1. 静态验证错误（来自 /validate）
 * 2. 沙箱运行时错误（来自回测结果）
 * 3. "AI 修复" 触发按钮
 */
export default function CodeErrorPanel({ errors, runtimeError, onAiFix, fixing }) {
  const hasErrors = errors.length > 0 || runtimeError

  if (!hasErrors) return null

  // 按类型分组
  const syntaxErrors    = errors.filter(e => e.type === 'syntax')
  const securityErrors  = errors.filter(e => e.type === 'security')
  const interfaceErrors = errors.filter(e => e.type === 'interface')

  const allErrorMessages = [
    ...errors.map(e => e.message),
    ...(runtimeError ? [runtimeError.message] : []),
  ].join('\n')

  return (
    <div className="border-t border-red-900/50 bg-red-950/20">
      <div className="flex items-center justify-between px-3 py-1.5">
        <span className="text-xs text-red-400 font-mono">
          ⚠ {errors.length + (runtimeError ? 1 : 0)} 个错误
        </span>
        <button
          onClick={() => onAiFix(allErrorMessages)}
          disabled={fixing}
          className="flex items-center gap-1 px-2.5 py-1 text-xs bg-orange-700 hover:bg-orange-600
                     disabled:opacity-50 rounded text-white transition-colors"
        >
          {fixing ? (
            <>
              <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
              AI 修复中...
            </>
          ) : '🔧 AI 一键修复'}
        </button>
      </div>

      <div className="px-3 pb-2 space-y-1 max-h-32 overflow-y-auto">
        {/* 语法错误 */}
        {syntaxErrors.map((e, i) => (
          <div key={i} className="flex gap-2 text-xs font-mono">
            <span className="text-red-500 shrink-0">语法</span>
            {e.line && <span className="text-gray-500 shrink-0">第{e.line}行</span>}
            <span className="text-red-300">{e.message}</span>
          </div>
        ))}
        {/* 安全违规 */}
        {securityErrors.map((e, i) => (
          <div key={i} className="flex gap-2 text-xs font-mono">
            <span className="text-orange-500 shrink-0">安全</span>
            {e.line && <span className="text-gray-500 shrink-0">第{e.line}行</span>}
            <span className="text-orange-300">{e.message}</span>
          </div>
        ))}
        {/* 接口缺失 */}
        {interfaceErrors.map((e, i) => (
          <div key={i} className="flex gap-2 text-xs font-mono">
            <span className="text-yellow-500 shrink-0">接口</span>
            <span className="text-yellow-300">{e.message}</span>
          </div>
        ))}
        {/* 运行时错误 */}
        {runtimeError && (
          <div className="text-xs font-mono">
            <div className="flex gap-2">
              <span className="text-red-500 shrink-0">运行时</span>
              {runtimeError.line && <span className="text-gray-500 shrink-0">第{runtimeError.line}行</span>}
              <span className="text-red-300">{runtimeError.message}</span>
            </div>
            {runtimeError.full_traceback && (
              <details className="mt-1">
                <summary className="text-gray-600 cursor-pointer hover:text-gray-400">查看完整 traceback</summary>
                <pre className="mt-1 text-gray-500 text-xs whitespace-pre-wrap overflow-x-auto bg-gray-900 p-2 rounded">
                  {runtimeError.full_traceback}
                </pre>
              </details>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
```

### Step 7: 更新 CodeEditor.jsx — 集成 Monaco 错误标记

在 `CodeEditor.jsx` 中修改 `Editor` 组件，添加 `onMount` 回调和 markers 更新：

```jsx
// frontend/src/components/studio/CodeEditor.jsx
// 在现有代码基础上修改

import Editor from '@monaco-editor/react'
import { useRef, useEffect } from 'react'

export default function CodeEditor({ value, onChange, generating, monacoMarkers = [] }) {
  const editorRef  = useRef(null)
  const monacoRef  = useRef(null)

  // 当 monacoMarkers 变化时，更新编辑器错误标记
  useEffect(() => {
    if (!editorRef.current || !monacoRef.current) return
    const model = editorRef.current.getModel()
    if (!model) return
    monacoRef.current.editor.setModelMarkers(model, 'strategy-validator', monacoMarkers)
  }, [monacoMarkers])

  function handleMount(editor, monaco) {
    editorRef.current  = editor
    monacoRef.current  = monaco
  }

  // ... 其余代码不变，Editor 组件添加 onMount={handleMount}
}
```

### Step 8: 更新 StrategyStudio.jsx — 集成 Debug 流程

在现有的 `StrategyStudio.jsx` 中添加以下逻辑（修改现有状态和函数）：

```jsx
// 新增状态
const [runtimeError, setRuntimeError] = useState(null)
const [fixing, setFixing]             = useState(false)

// 引入验证 hook
const { errors: codeErrors, monacoMarkers } = useCodeValidation(code)

// 新增：AI 修复函数
const handleAiFix = useCallback(async (errorMessage) => {
  if (!code) return
  setFixing(true)
  try {
    const res = await fetch('/api/strategy/fix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code,
        error_message: errorMessage,
        error_type: runtimeError ? 'runtime' : 'static',
      }),
    })
    const data = await res.json()
    if (data.code) {
      setCode(data.code)
      setRuntimeError(null)  // 清除运行时错误，等待重新验证
    }
  } catch (e) {
    console.error('AI 修复失败:', e)
  } finally {
    setFixing(false)
  }
}, [code, runtimeError])

// 修改 handleBacktest：回测失败时解析 runtime error
// 在 setBtError(job.error) 之前，尝试解析 traceback：
// const tbInfo = job.error?.includes('Traceback') ? parseTraceback(job.error) : null
// setRuntimeError(tbInfo)

// 在 CodeEditor 下方渲染 CodeErrorPanel：
// <CodeErrorPanel
//   errors={codeErrors}
//   runtimeError={runtimeError}
//   onAiFix={handleAiFix}
//   fixing={fixing}
// />
```

### Step 9: 运行测试

```bash
# 单元测试
python -m pytest tests/test_code_validator.py -v

# 手动测试：故意写错误代码验证修复流程
# 1. 在右侧编辑器写入: import os
# 2. 800ms 后应出现红色波浪线 + 错误面板
# 3. 点击"AI 一键修复"，错误应被自动删除

# 前端开发服务器验证
cd /Users/max/Developer/Bitget/frontend && npm run dev
```

### Step 10: Commit

```bash
git add bitget_bot/sandbox/code_validator.py \
        bitget_bot/strategy_router.py \
        frontend/src/hooks/useCodeValidation.js \
        frontend/src/components/studio/CodeErrorPanel.jsx \
        frontend/src/components/studio/CodeEditor.jsx \
        frontend/src/components/StrategyStudio.jsx \
        tests/test_code_validator.py
git commit -m "feat: add debug loop with real-time validation, Monaco markers, and AI auto-fix"
```

---

## Task 13: 现有策略迁移到 Strategy Studio

### 背景：接口差异分析

现有 `strategy.py` 使用的接口与新接口存在以下差异，需要精确适配：

```
现有接口                              新接口
──────────────────────────────────────────────────────────────
evaluate_bar_fixed(                   get_signal(
  o, c, v,                ────────→     df: DataFrame,
  m20, m60, m120,                       i: int,
  e20, e60, e120,                       params: dict
  i, squeeze_threshold               ) -> dict(4 bool keys)
) -> BarSignals(7 fields)

add_indicators(df)        ────────→  add_indicators(df)
（相同，直接复用）                    （需添加 is_bull/is_bear 列）
```

关键差异：
1. `evaluate_bar_fixed` 接收 9 个 numpy 数组 → `get_signal` 从 DataFrame 中提取
2. `BarSignals` 有 7 个字段（含 `long_tp_confirm`、`is_squeezed`）→ 新接口只需 4 个字段
3. `ta_value_when` 和 `crossover`/`crossunder` 辅助函数需要内联到 `get_signal` 中（不能独立定义为模块级函数，但可以定义为 `get_signal` 内的嵌套函数）

### 迁移验证策略

迁移正确性的黄金测试：对同一段 OHLCV 历史数据，**原始代码和迁移后代码的回测结果必须完全一致**（相同的交易次数、相同的每笔盈亏）。

**Files:**
- Create: `bitget_bot/strategies/ma_squeeze_studio.py`（迁移后的 Studio 格式策略）
- Create: `bitget_bot/strategies/__init__.py`
- Create: `bitget_bot/strategies/ma_squeeze_studio.md`（对应的 Markdown 描述文档）
- Create: `tests/test_strategy_migration.py`（等价性验证测试）

### Step 1: 写迁移等价性测试（先写测试，再写代码）

```python
# tests/test_strategy_migration.py
"""
验证迁移后策略与原始策略的回测结果完全等价。
测试通过 = 迁移正确。
"""
import pytest
import time
import pandas as pd

# 原始策略路径
from backtest import run_backtest as original_run_backtest
from bitget_bot.strategy import add_indicators as original_add_indicators

# 迁移后策略（新接口）
from bitget_bot.strategies.ma_squeeze_studio import (
    add_indicators as new_add_indicators,
    get_signal as new_get_signal,
)


def _make_test_df(n_bars=300) -> pd.DataFrame:
    """生成确定性测试数据（固定随机种子，可重复）。"""
    import numpy as np
    rng = np.random.default_rng(42)
    base = 35000.0
    closes = base + np.cumsum(rng.normal(0, 50, n_bars))
    opens  = closes - rng.normal(0, 20, n_bars)
    highs  = np.maximum(opens, closes) + rng.uniform(10, 80, n_bars)
    lows   = np.minimum(opens, closes) - rng.uniform(10, 80, n_bars)
    vols   = rng.uniform(50, 200, n_bars)
    base_ts = int(time.time() * 1000) - n_bars * 900_000
    timestamps = [base_ts + i * 900_000 for i in range(n_bars)]
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols,
    })


def _run_new_strategy(df: pd.DataFrame, squeeze_threshold=0.35):
    """用新接口运行回测（复制 sandbox_runner 的回测引擎逻辑）。"""
    import math
    import numpy as np

    d = new_add_indicators(df.copy())
    n = len(d)

    # 简化回测引擎（只记录交易方向和时间）
    position = 0
    entries = []   # [(direction, bar_idx)]
    exits   = []   # [(bar_idx,)]

    for i in range(1, n - 1):
        sig = new_get_signal(d, i, {"squeeze_threshold": squeeze_threshold})
        long_e  = bool(sig.get("long_entry", False))
        short_e = bool(sig.get("short_entry", False))
        close_l = bool(sig.get("close_long", False))
        close_s = bool(sig.get("close_short", False))

        new_pos = position
        if long_e:  new_pos = 1
        if short_e: new_pos = -1
        if position > 0 and close_l: new_pos = 0
        if position < 0 and close_s: new_pos = 0

        if position != 0 and new_pos != position:
            exits.append(i + 1)
            position = 0

        if new_pos != 0 and new_pos != position:
            entries.append(("long" if new_pos == 1 else "short", i + 1))
            position = new_pos

    return entries, exits


def test_indicator_columns_match():
    """新旧策略计算的指标列数值应完全一致。"""
    df = _make_test_df(200)
    old_df = original_add_indicators(df.copy())
    new_df = new_add_indicators(df.copy())

    for col in ["m20", "m60", "m120", "e20", "e60", "e120"]:
        pd.testing.assert_series_equal(
            old_df[col].reset_index(drop=True),
            new_df[col].reset_index(drop=True),
            check_names=False,
            rtol=1e-10,
        )


def test_trade_count_matches():
    """新旧策略在相同数据上产生的交易次数必须完全相同。"""
    df = _make_test_df(500)

    # 原始策略回测
    original_trades, _ = original_run_backtest(
        df.copy(),
        squeeze_threshold=0.35,
        initial_equity=10_000,
        leverage=5,
        margin_pct=100,
        fee_rate=0.0,
    )
    orig_closed = [t for t in original_trades if t.pnl_usdt is not None]

    # 新策略回测
    new_entries, new_exits = _run_new_strategy(df.copy(), squeeze_threshold=0.35)

    assert len(orig_closed) == len(new_exits), (
        f"交易次数不匹配: 原始={len(orig_closed)}, 迁移后={len(new_exits)}"
    )


def test_entry_directions_match():
    """新旧策略每笔交易的方向（多/空）必须一致。"""
    df = _make_test_df(500)

    original_trades, _ = original_run_backtest(
        df.copy(), squeeze_threshold=0.35,
        initial_equity=10_000, leverage=5, margin_pct=100, fee_rate=0.0,
    )
    orig_directions = [t.direction for t in original_trades if t.pnl_usdt is not None]
    new_entries, _ = _run_new_strategy(df.copy(), squeeze_threshold=0.35)
    new_directions = [e[0] for e in new_entries[:len(orig_directions)]]

    assert orig_directions == new_directions, (
        f"第一个方向不匹配处: "
        f"orig={orig_directions[:5]}, new={new_directions[:5]}"
    )
```

### Step 2: 运行确认测试失败

```bash
python -m pytest tests/test_strategy_migration.py -v
# 预期: ModuleNotFoundError: No module named 'bitget_bot.strategies'
```

### Step 3: 实现迁移后的策略代码

```python
# bitget_bot/strategies/ma_squeeze_studio.py
"""
均线密集 + 量价三K确认策略（反手版）—— Strategy Studio 格式

原始策略: bitget_bot/strategy.py (evaluate_bar_fixed 接口)
迁移为:   get_signal(df, i, params) 接口，供 Strategy Studio 沙箱使用

迁移说明：
- add_indicators(): 与原始版本计算逻辑完全一致，额外添加 is_bull/is_bear 列
- get_signal(): 将 evaluate_bar_fixed 的 9 个 numpy 数组参数改为从 DataFrame 提取，
  ta_value_when/crossover/crossunder 内联为局部函数，
  只返回入场/出场的 4 个布尔值（去掉 long_tp_confirm/is_squeezed 等诊断字段）
"""
import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算均线密集策略所需的所有指标：
    - m20/m60/m120: 简单移动均线
    - e20/e60/e120: 指数移动均线
    - gap_pct: 6条均线的带宽百分比（衡量密集程度）
    - is_bull/is_bear: 当根K线的涨跌标志（用于量价确认）
    """
    out = df.copy()
    c = out["close"].astype(float)

    out["m20"]  = c.rolling(20, min_periods=20).mean()
    out["m60"]  = c.rolling(60, min_periods=60).mean()
    out["m120"] = c.rolling(120, min_periods=120).mean()
    out["e20"]  = c.ewm(span=20, adjust=False).mean()
    out["e60"]  = c.ewm(span=60, adjust=False).mean()
    out["e120"] = c.ewm(span=120, adjust=False).mean()

    ma_cols = ["m20", "m60", "m120", "e20", "e60", "e120"]
    upper = out[ma_cols].max(axis=1)
    lower = out[ma_cols].min(axis=1)
    mid   = (upper + lower) / 2
    out["gap_pct"] = np.where(mid > 0, (upper - lower) / mid * 100.0, np.nan)

    # 涨跌标志（仅基于 open/close，不依赖前一根）
    o_vals = out["open"].astype(float)
    out["is_bull"] = c > o_vals
    out["is_bear"] = c < o_vals

    return out


def get_signal(df: pd.DataFrame, i: int, params: dict) -> dict:
    """
    评估第 i 根已收盘 bar 的交易信号（bar-close 语义）。

    入场信号在 bar i 收盘后产生 → 实际成交在 bar i+1 的开盘价（由回测引擎处理）。

    均线密集判断: 6条均线带宽 ≤ squeeze_threshold（默认 0.35%）
    做多: 均线密集 + 前一根金叉 + 放量 + 当前收盘高于 m20
    做空: 均线密集 + 前一根死叉 + 放量 + 当前收盘低于 m20
    平多: 收盘跌破 m120 OR 向下穿越 m60
    平空: 收盘突破 m120 OR 向上穿越 m60
    """
    squeeze_threshold = params.get("squeeze_threshold", 0.35)

    # 需要至少 121 根 bar（m120 需要 120 根预热，再往前看 1 根）
    if i < 121:
        return {"long_entry": False, "short_entry": False,
                "close_long": False, "close_short": False}

    # NaN 检查：任意指标缺失则不产生信号
    needed = ["m20", "m60", "m120", "e20", "e60", "e120", "gap_pct"]
    for col in needed:
        if pd.isna(df[col].iloc[i]):
            return {"long_entry": False, "short_entry": False,
                    "close_long": False, "close_short": False}

    # 提取 numpy 数组（切片到 i+1，确保不越界）
    c     = df["close"].astype(float).values[:i + 1]
    v     = df["volume"].astype(float).values[:i + 1]
    m20   = df["m20"].values[:i + 1]
    m60   = df["m60"].values[:i + 1]
    m120  = df["m120"].values[:i + 1]
    is_bull = df["is_bull"].values[:i + 1]
    is_bear = df["is_bear"].values[:i + 1]

    # ── 局部辅助函数（等价于原始 ta_value_when）──────────────
    def _value_when(cond_arr, src_arr, occurrence, at):
        """返回 cond_arr[:at+1] 中第 occurrence+1 次（从后数）为 True 时 src 的值。"""
        idxs = np.flatnonzero(cond_arr[:at + 1])
        if len(idxs) <= occurrence:
            return float("nan")
        return float(src_arr[idxs[-(occurrence + 1)]])

    def _crossover(series, ref, idx):
        """series[idx] 从 ≤ref 穿越到 >ref。"""
        if idx < 1:
            return False
        return series[idx] > ref[idx] and series[idx - 1] <= ref[idx - 1]

    def _crossunder(series, ref, idx):
        """series[idx] 从 ≥ref 穿越到 <ref。"""
        if idx < 1:
            return False
        return series[idx] < ref[idx] and series[idx - 1] >= ref[idx - 1]

    # ── 均线密集判断 ──────────────────────────────────────────
    is_squeezed = df["gap_pct"].iloc[i] <= squeeze_threshold

    # ── 量价信号（在前一根 bar i-1 处判断，复现 Pine 逻辑）───
    prev = i - 1

    # 前一根 bar 之前最近一根下跌/上涨 bar 的成交量
    last_down_vol = _value_when(is_bear, v, 1, prev)
    last_up_vol   = _value_when(is_bull, v, 1, prev)

    # K1确认：前一根 bar 金叉/死叉 + 放量
    long_k1_prev  = _crossover(c, m20, prev)  and v[prev] > last_down_vol
    short_k1_prev = _crossunder(c, m20, prev) and v[prev] > last_up_vol

    # ── 入场信号 ─────────────────────────────────────────────
    long_entry  = bool(is_squeezed and long_k1_prev  and c[i] > m20[i])
    short_entry = bool(is_squeezed and short_k1_prev and c[i] < m20[i])

    # ── 出场信号 ─────────────────────────────────────────────
    long_tp  = _crossunder(c, m60, i)   # 向下穿越 m60 → 多仓获利了结
    short_tp = _crossover(c, m60, i)    # 向上穿越 m60 → 空仓获利了结

    close_long  = bool((c[i] < m120[i]) or (c[i] >= m120[i] and long_tp))
    close_short = bool((c[i] > m120[i]) or (c[i] <= m120[i] and short_tp))

    return {
        "long_entry":  long_entry,
        "short_entry": short_entry,
        "close_long":  close_long,
        "close_short": close_short,
    }
```

### Step 4: 运行等价性测试（关键验证）

```bash
python -m pytest tests/test_strategy_migration.py -v
```

**预期输出：**
```
tests/test_strategy_migration.py::test_indicator_columns_match  PASSED
tests/test_strategy_migration.py::test_trade_count_matches      PASSED
tests/test_strategy_migration.py::test_entry_directions_match   PASSED
```

如果 `test_trade_count_matches` 或 `test_entry_directions_match` 失败，说明迁移逻辑有偏差，需要对照原始 `strategy.py` 逐行检查 `ta_value_when` / `crossover` 的实现。

### Step 5: 创建对应的 Markdown 描述文档

```markdown
<!-- bitget_bot/strategies/ma_squeeze_studio.md -->
# 均线密集 + 量价三K确认策略（反手版）

## 策略概述

Pine Script v5 移植版。核心思路：在6条均线高度压缩（密集）时，
等待量能放大的突破信号入场，用均线作为止损和获利了结线。

## 适用市场

BTC/USDT 永续合约，15 分钟 K 线

## 指标

- **SMA20/SMA60/SMA120**: 20、60、120 周期简单移动均线（收盘价）
- **EMA20/EMA60/EMA120**: 20、60、120 周期指数移动均线
- **均线带宽 (gap_pct)**: (6条均线最高值 - 最低值) / 中间值 × 100%，
  衡量均线密集程度，值越小越密集

## 核心条件：均线压缩（Squeeze）

6条均线的带宽必须 ≤ squeeze_threshold（默认 0.35%），
即6条均线几乎重叠在一起，表示市场处于盘整蓄势状态。

## 入场条件

### 做多信号（全部满足）
1. 当前 bar 均线处于压缩状态（gap_pct ≤ 0.35%）
2. 上一根 bar 收盘价向上穿越 SMA20（金叉）
3. 上一根 bar 的成交量 > 上上一根下跌K线的成交量（量能放大确认）
4. 当前 bar 收盘价 > SMA20（站稳均线上方）

### 做空信号（全部满足）
1. 当前 bar 均线处于压缩状态（gap_pct ≤ 0.35%）
2. 上一根 bar 收盘价向下穿越 SMA20（死叉）
3. 上一根 bar 的成交量 > 上上一根上涨K线的成交量（量能放大确认）
4. 当前 bar 收盘价 < SMA20（跌破均线下方）

## 出场条件

### 多仓出场（满足任一）
- 收盘价跌破 SMA120（硬止损）
- 收盘价向下穿越 SMA60（获利了结）

### 空仓出场（满足任一）
- 收盘价突破 SMA120（硬止损）
- 收盘价向上穿越 SMA60（获利了结）

## 执行时机

信号在 bar 收盘后生成，下一根 bar 开盘价成交（bar-close 语义）。

## 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| squeeze_threshold | 0.35 | 均线带宽阈值（%），越小要求越密集 |
```

### Step 6: 创建 Studio 预加载脚本

```python
# bitget_bot/strategies/load_default.py
"""
在 Strategy Studio 启动时，将内置策略的代码和文档预加载到前端。
通过新增 GET /api/strategy/builtin/{name} 端点暴露。
"""
from pathlib import Path
import inspect
from bitget_bot.strategies.ma_squeeze_studio import add_indicators, get_signal

_ROOT = Path(__file__).parent

BUILTIN_STRATEGIES = {
    "ma_squeeze": {
        "name":     "均线密集 + 量价确认（内置）",
        "markdown": (_ROOT / "ma_squeeze_studio.md").read_text(encoding="utf-8"),
        "code":     (_ROOT / "ma_squeeze_studio.py").read_text(encoding="utf-8"),
    }
}
```

在 `strategy_router.py` 中添加端点：

```python
# strategy_router.py 末尾追加
from bitget_bot.strategies.load_default import BUILTIN_STRATEGIES

@router.get("/builtin")
def list_builtin_strategies():
    """列出所有内置策略。"""
    return [{"id": k, "name": v["name"]} for k, v in BUILTIN_STRATEGIES.items()]

@router.get("/builtin/{strategy_id}")
def get_builtin_strategy(strategy_id: str):
    """获取内置策略的代码和文档。前端用于"加载示例"功能。"""
    strat = BUILTIN_STRATEGIES.get(strategy_id)
    if not strat:
        raise HTTPException(status_code=404, detail=f"内置策略 '{strategy_id}' 不存在")
    return strat
```

### Step 7: 前端添加"加载内置策略"按钮

在 `StrategyStudio.jsx` 的左侧操作栏中添加：

```jsx
// StrategyStudio.jsx 中，"▶ 生成代码" 按钮旁边添加：

const handleLoadBuiltin = useCallback(async () => {
  const res  = await fetch('/api/strategy/builtin/ma_squeeze')
  const data = await res.json()
  setMarkdown(data.markdown)
  setCode(data.code)
}, [])

// 在按钮区域添加：
<button
  onClick={handleLoadBuiltin}
  className="px-3 py-1.5 text-gray-400 hover:text-gray-200 text-sm
             border border-gray-700 hover:border-gray-500 rounded-md transition-colors"
>
  📦 加载内置策略
</button>
```

### Step 8: Commit

```bash
git add bitget_bot/strategies/ tests/test_strategy_migration.py
git commit -m "feat: migrate MA-squeeze strategy to Studio interface with equivalence tests"
```

---

## 更新后的新增文件清单

```
Bitget/
├── sandbox/
│   ├── Dockerfile.sandbox
│   └── sandbox_runner.py
├── bitget_bot/
│   ├── sandbox/
│   │   ├── __init__.py
│   │   ├── ast_validator.py
│   │   ├── code_validator.py         # ← Task 12 新增（结构化错误输出）
│   │   └── docker_executor.py
│   ├── strategies/                   # ← Task 13 新增
│   │   ├── __init__.py
│   │   ├── ma_squeeze_studio.py      # 迁移后策略（新接口）
│   │   ├── ma_squeeze_studio.md      # 对应 Markdown 文档
│   │   └── load_default.py           # 预加载内置策略
│   └── strategy_router.py
├── frontend/src/
│   ├── components/
│   │   ├── StrategyStudio.jsx
│   │   └── studio/
│   │       ├── MarkdownEditor.jsx
│   │       ├── CodeEditor.jsx        # ← Task 12 修改（Monaco markers）
│   │       ├── BacktestResults.jsx
│   │       └── CodeErrorPanel.jsx    # ← Task 12 新增
│   └── hooks/
│       ├── useWebSocket.js
│       └── useCodeValidation.js      # ← Task 12 新增
└── tests/
    ├── test_ast_validator.py
    ├── test_code_validator.py        # ← Task 12 新增
    ├── test_docker_executor.py
    └── test_strategy_migration.py   # ← Task 13 新增（等价性验证）
```

---

## 实施完成与验收报告（2026-04-03）

### Task 完成清单

| Task | 状态 | 说明 |
|------|------|------|
| 1 沙箱镜像 | 完成 | `sandbox/Dockerfile.sandbox` + `sandbox/sandbox_runner.py`；`docker build -f sandbox/Dockerfile.sandbox -t strategy-sandbox:latest sandbox/` |
| 2 AST 验证器 | 完成 | `bitget_bot/sandbox/ast_validator.py` + `tests/test_ast_validator.py` |
| 3 Docker 执行器 | 完成 | `bitget_bot/sandbox/docker_executor.py`（`docker run` + subprocess，非 Python SDK）+ `tests/test_docker_executor.py` |
| 4 策略 API | 完成 | `bitget_bot/strategy_router.py`：`/generate`、`/backtest`、`/backtest/{id}`、`/validate`、`/fix`、`/builtin`；`api.py` 已 `include_router` |
| 5 Docker 基建 | 完成 | 根 `Dockerfile` 安装 `docker.io`；`docker-compose.yml` 挂载 `docker.sock` |
| 6 前端导航 + Monaco | 完成 | `App.jsx` 双标签；`package.json` 含 `@monaco-editor/react` |
| 7 Markdown 编辑器 | 完成 | `frontend/src/components/studio/MarkdownEditor.jsx` |
| 8 Python 编辑器 | 完成 | `frontend/src/components/studio/CodeEditor.jsx`（Monaco markers） |
| 9 回测结果面板 | 完成 | `frontend/src/components/studio/BacktestResults.jsx` |
| 10 Strategy Studio 主页面 | 完成 | `frontend/src/components/StrategyStudio.jsx` |
| 11 集成验证 | 完成 | 见下方自动化测试 + 浏览器抽检 |
| 12 Debug 机制 | 完成 | `code_validator.py`、`/validate`、`/fix`、`useCodeValidation.js`、`CodeErrorPanel.jsx` |
| 13 策略迁移 | 完成 | `bitget_bot/strategies/ma_squeeze_studio.py` + `.md` + `load_default.py`；`tests/test_strategy_migration.py` |

### 本轮收尾修正（验收前）

1. **安全**：已从 `strategy_router.py` 移除硬编码的 DeepSeek API Key；仅使用环境变量 `DEEPSEEK_API_KEY`（`.env.example` 已补充说明，勿将真实密钥提交仓库）。
2. **默认策略**：`StrategyStudio` 挂载时同时拉取 `GET /api/strategy/builtin/ma_squeeze` 的 **Markdown + Python**，与「加载内置策略」一致。
3. **回测参数**：`BacktestRequest` 增加 `squeeze_threshold`，与 `ma_squeeze_studio.get_signal` 对齐；前端增加 Squeeze% 输入。
4. **运行时错误**：回测失败时解析错误串中的 `File "<strategy>", line N` 供错误面板展示。
5. **白名单**：允许 `from __future__ import annotations`，否则内置策略在 `/validate` 与 Monaco 下会误报安全错误。
6. **包结构**：新增 `bitget_bot/sandbox/__init__.py`、`pytest.ini`（`integration` marker）。

### 自动化测试结果（验收日）

```text
pytest tests/ -m "not integration"   → 15 passed
pytest tests/test_docker_executor.py -m integration → 3 passed
npm run build (frontend)             → success
docker build -f sandbox/Dockerfile.sandbox -t strategy-sandbox:latest sandbox/ → success
```

### 浏览器抽检（Cursor Browser MCP）

- 访问 `http://127.0.0.1:8080/`：标题 **Bitget Bot Dashboard**，存在 **✦ 策略工作台** 标签。
- 进入策略工作台后可见：**▶ 生成代码**、**📦 加载内置策略**、双 **Editor content**（Monaco）、回测参数含 **Squeeze%**、**▶ 运行回测**（代码加载后可点）。

### 已知限制 / 运维注意

- **AI 生成 / 修复**：未设置 `DEEPSEEK_API_KEY` 时 `/generate` 与 `/fix` 返回 500；本地在 `.env` 配置即可。
- **沙箱回测**：主机须安装 Docker 且已构建 `strategy-sandbox:latest`；compose 部署需挂载 `docker.sock`（见 `docker-compose.yml`）。
- **实盘线程**：本机验收时若 `.env` 中 Bitget 密钥无效，后台 bot 线程可能打错误日志，与 Strategy Studio 无直接关系。
