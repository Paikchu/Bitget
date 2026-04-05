# Backtesting Feedback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an AI-assisted backtesting feedback system for Strategy Studio that can evaluate stop-loss and take-profit quality across multiple backtest cases and produce structured optimization recommendations.

**Architecture:** Extend the existing sandbox backtest pipeline so a single backtest produces richer trade diagnostics, then add an experiment layer for parameter sweeps and scenario batches, and finally add an AI analysis layer that consumes structured statistics instead of raw free-form text. The frontend should present both the raw experiment results and the AI feedback in a stable, reviewable format.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Docker sandbox runner, React 19, Vite, Zustand, OpenAI-compatible SDK, ccxt, pandas, numpy

---

## Background

The current project already supports:

- Strategy generation and validation
- Sandbox backtesting through Docker
- Basic backtest summary and trade list display
- Strategy version history with latest backtest summary

The current gap is that the system can run a backtest, but it cannot yet:

- Explain why a strategy performed well or poorly
- Compare multiple parameter combinations or market scenarios
- Judge whether stop-loss or take-profit settings are too tight or too loose
- Produce structured next-step optimization guidance

This plan records the four tasks needed to close that gap.

## Task 1: Expand Backtest Diagnostics

**Purpose:** Make single-run backtest output rich enough for downstream analysis.

**Why this comes first:** AI analysis quality depends on diagnostic detail. With only total return, win rate, and drawdown, the system cannot make reliable judgments about stop-loss and take-profit behavior.

**Primary files to review:**

- `sandbox/sandbox_runner.py`
- `bitget_bot/strategy_router.py`
- `bitget_bot/sandbox/docker_executor.py`
- `frontend/src/components/BacktestPanel.jsx`
- `frontend/src/components/studio/BacktestResults.jsx`

**Required output changes:**

- Add explicit trade exit metadata:
  - `exit_reason`
  - `holding_bars`
  - `holding_minutes`
  - `bars_in_profit`
  - `bars_in_drawdown`
- Add per-trade excursion metrics:
  - `max_favorable_excursion_pct`
  - `max_adverse_excursion_pct`
  - `peak_profit_before_exit_pct`
  - `deepest_drawdown_before_exit_pct`
- Add richer summary metrics:
  - `avg_win_pct`
  - `avg_loss_pct`
  - `avg_win_usdt`
  - `avg_loss_usdt`
  - `expectancy_usdt`
  - `expectancy_pct`
  - `pnl_stddev`
  - `long_win_rate_pct`
  - `short_win_rate_pct`
  - `avg_holding_bars`
  - `max_consecutive_losses`
  - `recovery_factor`

**Design notes:**

- Keep calculations deterministic inside the sandbox runner.
- Keep the response JSON machine-friendly and stable.
- Do not let the model infer metrics that can be computed directly from trades.
- Preserve backward compatibility for the existing backtest panel where possible.

**Acceptance criteria:**

- A single backtest result includes enriched `summary` and `trades`.
- Existing backtest execution still works through Docker.
- Existing UI continues to render current summary fields without regression.
- New metrics are available for later experiment and AI layers.

## Task 2: Add Experiment and Scenario Backtesting

**Purpose:** Run multiple backtests in one logical batch so the system can compare strategy behavior across parameters and market conditions.

**Why this is necessary:** Stop-loss and take-profit quality cannot be judged from one run. The system needs cross-case evidence, including different parameter sets and different market windows.

**Primary files to review:**

- `bitget_bot/strategy_router.py`
- `bitget_bot/db.py`
- `sandbox/sandbox_runner.py`
- `tests/`

**New backend capabilities:**

- Add experiment entities:
  - `strategy_experiments`
  - `strategy_experiment_runs`
- Support batch execution over:
  - parameter grids
  - date windows
  - timeframe variants
  - scenario tags such as trend, range, volatile
- Add experiment-level summary aggregation:
  - best return
  - median return
  - worst drawdown
  - stability score
  - pass/fail count by scenario

**Suggested first experiment dimensions:**

- `stop_loss_pct`
- `take_profit_pct`
- `risk_reward_ratio`
- `trailing_stop_enabled`
- `timeframe`
- `days` or explicit date windows

**Suggested APIs:**

- `POST /api/strategy/experiments`
- `GET /api/strategy/experiments/{experiment_id}`
- `GET /api/strategy/experiments/{experiment_id}/runs`

**Design notes:**

- Start with synchronous batch orchestration backed by the existing job pattern.
- Persist experiment metadata and run results in SQLite.
- Keep scenario definitions explicit and versionable.
- Prefer grid search first; add walk-forward after the basic experiment flow is stable.

**Acceptance criteria:**

- A user can submit one strategy and receive multiple structured run results.
- Each run stores both its input parameter set and its output summary.
- The system can compare runs without reformatting raw text.
- The experiment result is durable across service restart.

## Task 3: Build the AI Feedback Layer

**Purpose:** Convert structured backtest and experiment results into actionable feedback about stop-loss, take-profit, robustness, and next-step optimization.

**Why this is the right AI role:** The model should not guess from raw charts or loosely formatted summaries. It should evaluate precomputed evidence using a fixed rubric and return structured findings.

**Primary files to review or create:**

- `bitget_bot/strategy_router.py`
- `bitget_bot/db.py`
- `bitget_bot/ai_feedback.py` or equivalent new module
- `tests/`

**Recommended analysis flow:**

1. Backend computes deterministic metrics.
2. Backend builds a compact structured analysis payload.
3. AI evaluates that payload against a rubric.
4. AI returns strict JSON output.
5. Backend stores the analysis and serves it to the frontend.

**AI output schema should include:**

- `overall_score`
- `stop_loss_assessment`
- `take_profit_assessment`
- `robustness_assessment`
- `top_issues`
- `recommended_experiments`
- `parameter_adjustment_hints`
- `confidence`
- `evidence`

**Recommended rubric examples:**

- Stop-loss too tight:
  - many losing trades with small adverse excursion before stop
  - high stop-out frequency followed by price recovery
- Stop-loss too loose:
  - average loss much larger than average win
  - long holding time on losing positions
- Take-profit too early:
  - large positive MFE but small realized gain
  - many trades exit before capturing follow-through
- Take-profit too late:
  - frequent profit giveback before exit
  - weak realized efficiency relative to peak favorable excursion

**Implementation guidance:**

- Use Structured Outputs so the response adheres to a fixed schema.
- Keep prompts short and evidence-heavy.
- Avoid sending full raw trade lists when aggregate features are sufficient.
- Record prompt version and analysis schema version for reproducibility.

**Acceptance criteria:**

- AI feedback returns valid structured JSON.
- Feedback is grounded in measurable evidence from backtest runs.
- The system can explain stop-loss and take-profit issues separately.
- The output can be rendered directly in the frontend without manual parsing.

## Task 4: Frontend Feedback and Comparison UI

**Purpose:** Show experiment results and AI feedback in a way that is readable, comparable, and useful for iterative strategy work.

**Why this matters:** If the user cannot inspect the evidence and compare runs clearly, the AI feedback becomes hard to trust.

**Primary files to review:**

- `frontend/src/components/BacktestPanel.jsx`
- `frontend/src/components/studio/BacktestResults.jsx`
- `frontend/src/store/botStore.js`
- related Strategy Studio components under `frontend/src/components/studio/`

**Recommended UI sections:**

- Experiment summary
- Parameter comparison table
- AI feedback summary
- Stop-loss assessment
- Take-profit assessment
- Robustness assessment
- Recommended next experiments

**Recommended UI behaviors:**

- Show experiment status separately from AI analysis status.
- Allow opening individual run details.
- Highlight evidence, not only conclusions.
- Preserve current backtest flow for users who only want a single run.

**Recommended first-release constraints:**

- No automatic strategy code rewrite yet
- No hidden parameter mutation by AI
- No free-form markdown blobs as the only output

**Acceptance criteria:**

- A user can run an experiment and view the run matrix.
- A user can inspect AI feedback tied to that experiment.
- The UI clearly separates raw result metrics from AI interpretation.
- The existing backtest panel remains usable for the current workflow.

## Delivery Order

Implement in this order:

1. Task 1: Expand backtest diagnostics
2. Task 2: Add experiment and scenario backtesting
3. Task 3: Build the AI feedback layer
4. Task 4: Frontend feedback and comparison UI

This order keeps the system grounded in deterministic data before adding inference and presentation layers.

## Risks and Constraints

- The current in-memory job store is not sufficient for long-running experiment workflows.
- If stop-loss and take-profit are not modeled explicitly in the runner, AI feedback will remain weak.
- Parameter optimization without out-of-sample testing can lead to overfitting.
- Large prompt payloads may increase latency and cost; aggregation must happen before AI analysis.
- Existing single-backtest UI should not regress while experiment support is added.

## Recommended MVP Boundary

For the first release, limit scope to:

- Enriched single-run diagnostics
- Parameter-grid experiment execution
- Structured AI feedback for stop-loss, take-profit, and robustness
- Frontend display for experiment results and AI conclusions

Defer these to a later phase:

- Automatic code rewriting
- Full walk-forward optimization engine
- Multi-model analysis
- Advanced portfolio-level evaluation across symbols

## Verification

Before closing implementation, verify all of the following:

- Backend tests cover enriched summary and trade metrics
- Experiment persistence survives service restart
- AI output validates against the declared schema
- Browser flow works end-to-end from run submission to feedback rendering
- Docker rebuild succeeds with the new backend and frontend changes

## Expected Outcome

After these four tasks are complete, the project will move from “can run a backtest” to “can evaluate a strategy systematically across scenarios and explain what to improve next,” while staying compatible with the current Strategy Studio architecture.
