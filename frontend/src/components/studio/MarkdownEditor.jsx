import Editor from '@monaco-editor/react'

export const DEFAULT_MARKDOWN = `# 均线密集 + 量价三K确认策略（反手版）

## 策略概述

Pine Script v5 移植版。核心思路：在6条均线高度压缩（密集）时，等待量能放大的突破信号入场，用均线作为止损和获利了结线。

## 适用市场

BTC/USDT 永续合约，15 分钟 K 线

## 指标

- **SMA20 / SMA60 / SMA120**: 20、60、120 周期简单移动均线（收盘价）
- **EMA20 / EMA60 / EMA120**: 20、60、120 周期指数移动均线
- **均线带宽 (gap_pct)**: (6条均线最高值 - 最低值) / 中间值 × 100%，衡量均线密集程度

## 核心条件：均线压缩（Squeeze）

6条均线的带宽必须 ≤ squeeze_threshold（默认 0.35%），即6条均线几乎重叠在一起。

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
            padding: { top: 12, bottom: 12 },
          }}
        />
      </div>
    </div>
  )
}
