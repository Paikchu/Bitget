# Strategy Versioning Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 Strategy Studio 增加可持久化的版本控制能力，在用户成功生成一版新策略代码后，自动保存该版本对应的策略文档与策略代码，并支持查询与回滚。

**Architecture:** 采用后端主导的不可变快照方案。每次 `POST /api/strategy/generate` 成功后，由 FastAPI 在 SQLite 中追加一条版本记录，保存 Markdown、生成出的 Python 代码、来源、创建时间和版本号。前端只负责展示当前版本与历史版本列表，不承担版本真实性。后续“恢复版本”通过读取历史快照并回填编辑器实现，不直接覆写旧记录，而是将恢复后的内容作为当前工作副本。

**Tech Stack:** Python 3.11, FastAPI, SQLite, React 18, Zustand, Monaco Editor, pytest

---

### Task 1: 定义版本模型与数据表

**Files:**
- Modify: `bitget_bot/db.py`
- Test: `tests/test_strategy_versioning_db.py`

**Step 1: 新增策略版本表**

在 `init_db()` 中增加 `strategy_versions` 表，字段至少包含：

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
version_no INTEGER NOT NULL,
source TEXT NOT NULL,              -- 'generate' | 'builtin_import' | 'restore' | 'fix'
title TEXT,                        -- 可选，从 markdown 第一行标题提取
markdown TEXT NOT NULL,
code TEXT NOT NULL,
model TEXT,                        -- deepseek-chat 等
parent_version_id INTEGER,         -- 便于未来做来源追踪
created_at TEXT NOT NULL
```

约束建议：

- `version_no` 唯一递增
- `markdown` 与 `code` 必须非空
- `source` 限制在白名单集合

**Step 2: 补充 DB 访问函数**

在 `bitget_bot/db.py` 新增：

- `create_strategy_version(...) -> dict`
- `list_strategy_versions(limit=50, offset=0) -> list[dict]`
- `get_strategy_version(version_id: int) -> Optional[dict]`
- `get_latest_strategy_version() -> Optional[dict]`
- `get_next_strategy_version_no(conn) -> int`

实现原则：

- 写入时在同一事务内计算下一个 `version_no`
- 返回统一字典结构，供 API 直接输出
- 不做“就地更新历史版本”，历史记录保持不可变

**Step 3: 写数据库层单元测试**

新增测试覆盖：

- 初始化后存在 `strategy_versions` 表
- 连续写入两次时 `version_no` 递增
- `list/get_latest` 返回顺序正确
- `markdown/code/source/model` 均可正确持久化

**Step 4: 运行数据库测试**

Run: `pytest tests/test_strategy_versioning_db.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add bitget_bot/db.py tests/test_strategy_versioning_db.py
git commit -m "feat: add strategy version persistence"
```


### Task 2: 在生成链路中自动落版本

**Files:**
- Modify: `bitget_bot/strategy_router.py`
- Test: `tests/test_strategy_router.py`

**Step 1: 扩展 `/api/strategy/generate` 返回结构**

在生成成功后：

1. 正常清洗并规范化代码
2. 调用数据库层写入一条新版本
3. 返回：

```json
{
  "code": "...",
  "model": "deepseek-chat",
  "version": {
    "id": 12,
    "version_no": 12,
    "source": "generate",
    "created_at": "..."
  }
}
```

**Step 2: 明确自动保存触发规则**

本轮实现采用以下规则：

- 只有“生成代码成功”时自动保存版本
- 生成失败不保存
- 单纯在前端手动编辑 Markdown/代码不自动保存
- `AI fix`、`加载内置策略`、`恢复历史版本` 先不自动形成新版本，后续可扩展

这样可以严格满足你当前描述的“创建并生成过一版全新的代码之后自动保存一版记录”，同时避免每次编辑都产生噪音版本。

**Step 3: 增加 router 测试**

在 `tests/test_strategy_router.py` 增加：

- mock DeepSeek 返回代码
- mock 数据库写入函数或使用临时 DB
- 断言成功生成后调用了版本创建
- 断言返回体含 `version`
- 断言 DeepSeek 报错时不会写版本

**Step 4: 运行策略路由测试**

Run: `pytest tests/test_strategy_router.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add bitget_bot/strategy_router.py tests/test_strategy_router.py
git commit -m "feat: version generated strategies"
```


### Task 3: 增加版本查询与恢复 API

**Files:**
- Modify: `bitget_bot/strategy_router.py`
- Test: `tests/test_strategy_router.py`

**Step 1: 新增版本读取接口**

新增以下 API：

- `GET /api/strategy/versions`
- `GET /api/strategy/versions/{version_id}`

返回字段至少包含：

- `id`
- `version_no`
- `title`
- `source`
- `model`
- `created_at`
- `markdown`
- `code`

列表接口可先直接返回最近 50 条，按 `version_no DESC` 排序。

**Step 2: 定义恢复策略**

恢复操作推荐先不单独新增“写操作 API”，而是：

- 前端点选某个历史版本
- 调用 `GET /api/strategy/versions/{id}`
- 将返回的 `markdown` 与 `code` 回填到编辑器

这一阶段只做“恢复到当前工作区”，不自动新建版本。用户若基于恢复内容再次点击“生成代码”，才会形成新的生成版本。

**Step 3: 增加接口测试**

覆盖：

- 列表按最新优先排序
- 详情接口返回完整快照
- 不存在的版本返回 404

**Step 4: 运行测试**

Run: `pytest tests/test_strategy_router.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add bitget_bot/strategy_router.py tests/test_strategy_router.py
git commit -m "feat: add strategy version history apis"
```


### Task 4: 在前端展示版本历史

**Files:**
- Modify: `frontend/src/components/StrategyStudio.jsx`
- Modify: `frontend/src/store/botStore.js`
- Create: `frontend/src/components/studio/VersionHistoryPanel.jsx`

**Step 1: 扩展前端状态**

在 store 中新增：

- `strategyVersions`
- `currentVersion`
- `setStrategyVersions`
- `setCurrentVersion`

注意：`strategyCode` 仍保留，供回测面板使用。

**Step 2: 在生成成功后同步当前版本**

`handleGenerate()` 成功后：

- 更新 `code`
- 更新 `currentVersion`
- 重新拉取版本列表

**Step 3: 新增版本历史 UI**

在 Strategy Studio 右侧或代码区下方增加轻量历史面板，展示：

- `V12`
- 生成时间
- 来源（当前阶段大概率都是“生成”）
- 标题（从 Markdown 首标题提取）

交互先做两项：

- `查看/恢复`
- 当前版本高亮

不做复杂 diff，避免第一版过重。

**Step 4: 恢复历史版本**

点击某个版本后：

- 拉取版本详情
- 用其 `markdown` 与 `code` 覆盖当前编辑器内容
- 更新 `currentVersion`

这里要明确提示：这是把历史版本载入工作区，不是覆盖数据库中的旧版本。

**Step 5: 前端验证**

Run: `npm --prefix frontend run build`
Expected: build success

**Step 6: Commit**

```bash
git add frontend/src/components/StrategyStudio.jsx frontend/src/store/botStore.js frontend/src/components/studio/VersionHistoryPanel.jsx
git commit -m "feat: show strategy version history"
```


### Task 5: 验收与边界行为

**Files:**
- Modify: `docs/strategy-studio-test-flow.md`
- Optional Test: `output/playwright/e2e/*`

**Step 1: 更新测试流程文档**

在 `docs/strategy-studio-test-flow.md` 增加以下验收点：

- 输入新策略文档并点击“生成代码”
- 确认数据库新增一条版本记录
- 确认历史面板出现新版本
- 恢复旧版本后，文档与代码一起回填
- 基于恢复版本再次生成，产生新的版本号

**Step 2: 手工验证数据库**

Run:

```bash
sqlite3 data/bot.db "SELECT id, version_no, source, created_at FROM strategy_versions ORDER BY version_no DESC LIMIT 10;"
```

Expected: 能看到新增版本记录

**Step 3: 全量测试**

Run:

```bash
pytest tests/test_strategy_router.py tests/test_strategy_versioning_db.py -v
npm --prefix frontend run build
```

Expected: 全部通过

**Step 4: Commit**

```bash
git add docs/strategy-studio-test-flow.md
git commit -m "docs: add strategy versioning test flow"
```


## 设计决策与取舍

1. 推荐使用 SQLite 快照表，而不是 Git 版本控制。
原因：当前系统已经有 SQLite 持久层，版本记录需要和运行中的 Web 应用直接联动；Git 更适合开发者代码，不适合在线用户策略版本。

2. 推荐保存完整快照，而不是只保存 diff。
原因：Markdown 与 Python 文本体量小，完整快照实现简单、读取快、恢复直接，后续如需 diff 可在展示层计算。

3. 第一阶段只把“生成成功”定义为自动版本点。
原因：这是最符合你当前业务语义的事件边界，且不会因为用户打字或修错而产生成堆碎片版本。

4. 恢复历史版本先做“载入工作区”，不做“恢复即新建版本”。
原因：这样语义更清晰，也更容易避免误操作导致版本号膨胀。若后续希望保留恢复行为痕迹，再追加 `source='restore'` 的派生版本即可。


## 开放问题

1. `AI fix` 成功后是否也应该自动存一版？
当前计划默认不自动保存，只在用户重新生成时落版本。

2. `加载内置策略` 是否需要作为 `source='builtin_import'` 存历史？
当前计划默认不保存。

3. 是否需要“版本备注 / 用户命名”？
当前计划默认只从 Markdown 一级标题提取标题，不额外增加输入成本。


## 推荐落地顺序

1. 先完成 DB 表与生成链路自动保存
2. 再补版本查询 API
3. 最后接前端历史面板与恢复交互

这样即使前端还没做完，后端版本数据也已经开始正确沉淀。
