import importlib


def _load_db_module(monkeypatch, tmp_path):
    db_path = tmp_path / "strategy_versions.db"
    monkeypatch.setenv("DB_PATH", str(db_path))

    import bitget_bot.db as db_module

    return importlib.reload(db_module)


def test_init_db_creates_strategy_versions_table(monkeypatch, tmp_path):
    db = _load_db_module(monkeypatch, tmp_path)

    db.init_db()

    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_versions'"
        ).fetchone()

    assert row is not None

    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_version_backtests'"
        ).fetchone()

    assert row is not None

    with db._get_conn() as conn:
        experiments = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_experiments'"
        ).fetchone()
        runs = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_experiment_runs'"
        ).fetchone()
        feedback = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_experiment_feedback'"
        ).fetchone()

    assert experiments is not None
    assert runs is not None
    assert feedback is not None


def test_init_db_seeds_builtin_strategy_once_for_empty_database(monkeypatch, tmp_path):
    db = _load_db_module(monkeypatch, tmp_path)

    db.init_db()
    db.init_db()

    listing = db.list_strategy_versions()
    detail = db.get_latest_strategy_version()

    assert len(listing) == 1
    assert detail["version_no"] == 1
    assert detail["source"] == "builtin_import"
    assert detail["parent_version_id"] is None
    assert detail["model"] is None
    assert detail["markdown"]
    assert detail["code"]


def test_create_strategy_version_increments_version_numbers(monkeypatch, tmp_path):
    db = _load_db_module(monkeypatch, tmp_path)
    db.init_db()

    first = db.create_strategy_version(
        markdown="# First",
        code="print('first')",
        source="generate",
        model="deepseek-chat",
    )
    second = db.create_strategy_version(
        markdown="# Second",
        code="print('second')",
        source="generate",
        model="deepseek-chat",
        parent_version_id=first["id"],
    )

    assert first["version_no"] == 2
    assert second["version_no"] == 3
    assert second["parent_version_id"] == first["id"]


def test_list_and_get_latest_strategy_versions(monkeypatch, tmp_path):
    db = _load_db_module(monkeypatch, tmp_path)
    db.init_db()

    db.create_strategy_version(
        markdown="# V1",
        code="print('v1')",
        source="generate",
        model="deepseek-chat",
    )
    created = db.create_strategy_version(
        markdown="# V2",
        code="print('v2')",
        source="restore",
        model=None,
    )

    latest = db.get_latest_strategy_version()
    listing = db.list_strategy_versions()
    detail = db.get_strategy_version(created["id"])

    assert latest["version_no"] == 3
    assert listing[0]["version_no"] == 3
    assert listing[1]["version_no"] == 2
    assert listing[2]["version_no"] == 1
    assert detail["markdown"] == "# V2"
    assert detail["code"] == "print('v2')"
    assert detail["source"] == "restore"


def test_latest_backtest_summary_is_joined_to_version_listing(monkeypatch, tmp_path):
    db = _load_db_module(monkeypatch, tmp_path)
    db.init_db()

    created = db.create_strategy_version(
        markdown="# V1",
        code="print('v1')",
        source="generate",
        model="deepseek-chat",
    )

    db.record_strategy_version_backtest(
        strategy_version_id=created["id"],
        job_id="job-1",
        summary={"total_return_pct": 12.5, "total_trades": 8},
    )
    db.record_strategy_version_backtest(
        strategy_version_id=created["id"],
        job_id="job-2",
        summary={"total_return_pct": 18.0, "total_trades": 11},
    )

    listing = db.list_strategy_versions()
    detail = db.get_strategy_version(created["id"])

    assert listing[0]["latest_backtest_summary"]["total_return_pct"] == 18.0
    assert listing[0]["latest_backtest_at"] is not None
    assert detail["latest_backtest_summary"]["total_trades"] == 11


def test_experiment_runs_and_feedback_are_persisted(monkeypatch, tmp_path):
    db = _load_db_module(monkeypatch, tmp_path)
    db.init_db()

    version = db.create_strategy_version(
        markdown="# V1",
        code="print('v1')",
        source="generate",
        model="deepseek-chat",
    )

    experiment = db.create_strategy_experiment(
        strategy_version_id=version["id"],
        strategy_code="print('v1')",
        config={
            "symbol": "BTC/USDT:USDT",
            "parameter_grid": {"timeframe": ["15m"], "days": [90]},
        },
        scenario_summary=["15m-90d"],
        job_id="exp-1",
    )

    db.add_strategy_experiment_run(
        experiment_id=experiment["id"],
        run_key="run-1",
        params={"timeframe": "15m", "days": 90},
        scenario_tag="15m-90d",
        result={
            "summary": {"total_return_pct": 3.5, "max_drawdown_pct": 2.0},
            "trades": [{"direction": "long"}],
        },
    )
    db.save_strategy_experiment_feedback(
        experiment_id=experiment["id"],
        feedback={
            "overall_score": 78,
            "confidence": 0.72,
            "top_issues": [],
            "evidence": [],
        },
        prompt_version="v1",
        schema_version="feedback.v1",
        model="deepseek-chat",
    )

    loaded = db.get_strategy_experiment(experiment["id"])
    runs = db.list_strategy_experiment_runs(experiment["id"])
    feedback = db.get_strategy_experiment_feedback(experiment["id"])

    assert loaded["job_id"] == "exp-1"
    assert loaded["scenario_summary"] == ["15m-90d"]
    assert runs[0]["scenario_tag"] == "15m-90d"
    assert runs[0]["result"]["summary"]["total_return_pct"] == 3.5
    assert feedback["feedback"]["overall_score"] == 78
