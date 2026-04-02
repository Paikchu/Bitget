from bitget_bot.sandbox.code_validator import validate_code_full, parse_traceback


def test_syntax_error_has_line():
    result = validate_code_full("def foo(\n    pass\n")
    assert result["valid"] is False
    syntax_errs = [e for e in result["errors"] if e["type"] == "syntax"]
    assert len(syntax_errs) == 1
    assert syntax_errs[0]["line"] is not None


def test_security_error_has_line():
    code = "import numpy as np\nimport os\n\ndef add_indicators(df): return df\ndef get_signal(df,i,p): return {}\n"
    result = validate_code_full(code)
    sec_errs = [e for e in result["errors"] if e["type"] == "security"]
    assert any(e["line"] == 2 for e in sec_errs)


def test_valid_code_passes():
    code = (
        "import numpy as np\n"
        "import pandas as pd\n"
        "\n"
        "def add_indicators(df):\n"
        "    return df.copy()\n"
        "\n"
        "def get_signal(df, i, params):\n"
        "    return {'long_entry': False, 'short_entry': False, 'close_long': False, 'close_short': False}\n"
    )
    result = validate_code_full(code)
    assert result["valid"] is True


def test_missing_interface_functions():
    code = "import numpy as np\ndef add_indicators(df): return df\n"
    result = validate_code_full(code)
    assert any("get_signal" in e["message"] for e in result["errors"])


def test_parse_traceback_extracts_line():
    tb = (
        'Traceback (most recent call last):\n'
        '  File "<strategy>", line 8, in get_signal\n'
        "NameError: name 'sma20' is not defined\n"
    )
    info = parse_traceback(tb)
    assert info["line"] == 8
    assert "sma20" in info["message"]
