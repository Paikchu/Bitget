from bitget_bot.sandbox.ast_validator import validate_strategy_code

def test_allows_numpy_pandas():
    code = "import numpy as np\nimport pandas as pd\n\ndef add_indicators(df): return df\ndef get_signal(df,i,p): return {}\n"
    assert validate_strategy_code(code) == []

def test_blocks_os_import():
    code = "import os\ndef add_indicators(df): return df\ndef get_signal(df,i,p): return {}\n"
    errors = validate_strategy_code(code)
    assert any("os" in e for e in errors)

def test_blocks_subprocess():
    code = "import subprocess\ndef add_indicators(df): return df\ndef get_signal(df,i,p): return {}\n"
    assert len(validate_strategy_code(code)) > 0

def test_blocks_exec_call():
    code = "exec('import os')\ndef add_indicators(df): return df\ndef get_signal(df,i,p): return {}\n"
    errors = validate_strategy_code(code)
    assert any("exec" in e for e in errors)

def test_blocks_dunder_access():
    code = "x = (1).__class__.__bases__\ndef add_indicators(df): return df\ndef get_signal(df,i,p): return {}\n"
    errors = validate_strategy_code(code)
    assert len(errors) > 0

def test_catches_syntax_error():
    code = "def foo(\n"
    errors = validate_strategy_code(code)
    assert len(errors) > 0

def test_requires_add_indicators():
    code = "import numpy as np\ndef get_signal(df, i, p): return {}\n"
    errors = validate_strategy_code(code)
    assert any("add_indicators" in e for e in errors)

def test_requires_get_signal():
    code = "import numpy as np\ndef add_indicators(df): return df\n"
    errors = validate_strategy_code(code)
    assert any("get_signal" in e for e in errors)
