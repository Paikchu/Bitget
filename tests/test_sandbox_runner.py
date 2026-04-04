import io
import json

from sandbox import sandbox_runner


def test_main_reads_payload_from_file(tmp_path, monkeypatch):
    payload = {
        "strategy_code": (
            "import pandas as pd\n"
            "def add_indicators(df):\n"
            "    return df\n\n"
            "def get_signal(df, i, params):\n"
            "    return {'long_entry': False, 'short_entry': False, 'close_long': False, 'close_short': False}\n"
        ),
        "ohlcv": [
            [1711929600000, 100.0, 101.0, 99.0, 100.5, 10.0],
            [1711930500000, 100.5, 102.0, 100.0, 101.5, 11.0],
            [1711931400000, 101.5, 103.0, 101.0, 102.0, 12.0],
        ],
        "params": {"initial_equity": 10000, "leverage": 1, "fee_rate": 0.0005, "margin_pct": 100},
    }
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    stdout = io.StringIO()
    monkeypatch.setattr("sys.argv", ["sandbox_runner.py", str(payload_path)])
    monkeypatch.setattr("sys.stdout", stdout)

    sandbox_runner.main()

    result = json.loads(stdout.getvalue())
    assert result["success"] is True
    assert "summary" in result
