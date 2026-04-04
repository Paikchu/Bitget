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

    assert first["version_no"] == 1
    assert second["version_no"] == 2
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

    assert latest["version_no"] == 2
    assert listing[0]["version_no"] == 2
    assert listing[1]["version_no"] == 1
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
