# Phase 3 — React 前端 Dashboard

> 目标：构建一个专业的交易监控界面，实时展示 K 线图（含信号标注）、权益曲线、历史交易记录和回测面板。

---

## 1. 技术栈

| 组件 | 库 | 说明 |
|------|-----|------|
| 框架 | **React 18 + Vite** | 快速开发，热更新 |
| 样式 | **Tailwind CSS** | 深色主题，快速排版 |
| K 线图 | **TradingView Lightweight Charts** | 专业金融图表，免费开源 |
| 其他图表 | **Recharts** | 权益曲线折线图 |
| 状态管理 | **Zustand** | 轻量，适合中小型项目 |
| HTTP 请求 | **SWR** | 自动缓存 + 轮询，无需手写 useEffect |
| 实时通信 | 原生 **WebSocket** | 无需额外库 |
| 日期处理 | **dayjs** | 轻量时间格式化 |

---

## 2. 项目初始化

```bash
# 在 Bitget 根目录下创建前端项目
npm create vite@latest frontend -- --template react
cd frontend
npm install

# 安装依赖
npm install tailwindcss @tailwindcss/vite
npm install lightweight-charts recharts
npm install zustand swr
npm install dayjs
```

### 目录结构

```
frontend/
├── src/
│   ├── components/
│   │   ├── StatusBar.jsx        # 顶部状态栏
│   │   ├── CandleChart.jsx      # K 线图（含信号标注）
│   │   ├── EquityChart.jsx      # 权益曲线
│   │   ├── StatsPanel.jsx       # 统计摘要卡片
│   │   ├── TradesTable.jsx      # 历史交易表格
│   │   └── BacktestPanel.jsx    # 回测面板
│   ├── hooks/
│   │   ├── useWebSocket.js      # WebSocket 连接管理
│   │   └── useApi.js            # SWR 封装的 API 请求
│   ├── store/
│   │   └── botStore.js          # Zustand 全局状态
│   ├── App.jsx
│   └── main.jsx
├── index.html
├── vite.config.js               # 配置 API 代理
└── package.json
```

---

## 3. 页面布局

整体采用深色主题（类似 TradingView/Binance 风格）。

```
┌──────────────────────────────────────────────────────────────┐
│  StatusBar: [BTC/USDT 42,350 ▲]  [状态: 运行中 ●]  [持仓: 多] │
├──────────────────┬───────────────────────────────────────────┤
│  EquityChart     │                                           │
│  $10,234  +2.34% │        CandleChart (K 线图)              │
│  [折线图]        │   入场▲绿色标注  平仓▼红色标注           │
├──────────────────┴───────────────────────────────────────────┤
│                    StatsPanel                                  │
│  总交易 48 | 胜率 62.5% | 盈亏比 1.84 | 最大回撤 -8.2%      │
├──────────────────────────────────────────────────────────────┤
│                   TradesTable                                  │
│  # | 方向 | 开仓时间 | 开仓价 | 平仓价 | PnL% | PnL(USDT)   │
├──────────────────────────────────────────────────────────────┤
│                  BacktestPanel                                 │
│  [参数配置] [运行回测]  → 结果：权益曲线 + 统计摘要          │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. 核心组件实现要点

### 4.1 `CandleChart.jsx` — K 线图

使用 **TradingView Lightweight Charts**：

```jsx
import { createChart } from 'lightweight-charts';

// 在 K 线上叠加交易信号标记
chart.addSeries(CandlestickSeries);

// 入场标记（绿色向上箭头）
series.setMarkers([
  {
    time: trade.entry_time,
    position: 'belowBar',
    color: '#22c55e',
    shape: 'arrowUp',
    text: `Long @${trade.entry_price}`,
  },
  // 平仓标记（红色向下箭头）
  {
    time: trade.exit_time,
    position: 'aboveBar',
    color: '#ef4444',
    shape: 'arrowDown',
    text: `Exit @${trade.exit_price}`,
  }
]);
```

**关键功能：**
- 从 `GET /api/ohlcv` 获取 K 线数据
- 从 `GET /api/trades` 获取交易记录，叠加为标注
- WebSocket 收到 `trade_open` / `trade_close` 事件时，实时在图上添加新标记

### 4.2 `EquityChart.jsx` — 权益曲线

使用 **Recharts**：

```jsx
<ResponsiveContainer width="100%" height={160}>
  <LineChart data={equityPoints}>
    <XAxis dataKey="ts" tickFormatter={d => dayjs(d).format('MM/DD')} />
    <YAxis domain={['auto', 'auto']} />
    <Tooltip formatter={(v) => `$${v.toFixed(2)}`} />
    <Line
      type="monotone"
      dataKey="equity"
      stroke="#22c55e"
      dot={false}
      strokeWidth={2}
    />
    {/* 初始资金基准线 */}
    <ReferenceLine y={initialEquity} stroke="#6b7280" strokeDasharray="4 4" />
  </LineChart>
</ResponsiveContainer>
```

### 4.3 `TradesTable.jsx` — 交易记录表

- 分页加载，每页 50 条
- PnL 正数显示绿色，负数显示红色
- 未平仓的交易高亮显示，实时跟踪浮动盈亏
- 支持按方向（多/空）筛选

### 4.4 `useWebSocket.js` — WebSocket Hook

```js
export function useWebSocket(url) {
  const { updateTrade, updateEquity, setStatus } = useBotStore();

  useEffect(() => {
    const ws = new WebSocket(url);

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      switch (msg.type) {
        case 'trade_open':
        case 'trade_close':
          updateTrade(msg.data);
          break;
        case 'equity_update':
          updateEquity(msg.data);
          break;
        case 'bot_status':
          setStatus(msg.data);
          break;
      }
    };

    // 断线自动重连（3 秒后）
    ws.onclose = () => setTimeout(() => reconnect(), 3000);

    return () => ws.close();
  }, [url]);
}
```

### 4.5 `BacktestPanel.jsx` — 回测面板

- 表单配置：天数、squeeze 阈值、初始资金、杠杆
- 点击「运行回测」→ `POST /api/backtest` → 轮询 `GET /api/backtest/{job_id}`
- 结果展示：权益曲线 + 统计摘要 + 完整交易列表

---

## 5. Vite 代理配置

开发时前端运行在 `localhost:5173`，后端在 `localhost:8080`，需要配置代理避免跨域：

```js
// vite.config.js
export default {
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
      },
    },
  },
};
```

---

## 6. 构建与集成

前端构建后，由 FastAPI 直接托管静态文件（无需单独的 Nginx）：

```bash
# 构建前端
cd frontend && npm run build
# 产物在 frontend/dist/
```

在 `api.py` 中挂载：

```python
from fastapi.staticfiles import StaticFiles

# 挂载在最后，避免覆盖 /api 路由
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="static")
```

部署后访问 `http://服务器IP:8080` 即可看到完整 Dashboard。

---

## 7. 验证方式

```bash
cd frontend
npm run dev
# 访问 http://localhost:5173
```

检查清单：
- [ ] K 线图正常加载，入场/平仓标注可见
- [ ] 权益曲线随时间延伸
- [ ] 交易记录表格可分页
- [ ] WebSocket 连接状态（右下角显示连接指示）
- [ ] 回测面板可以提交并展示结果
