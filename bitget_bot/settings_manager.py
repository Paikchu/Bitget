from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import ccxt
from dotenv import dotenv_values
from openai import OpenAI


ENV_FIELD_ORDER = [
    "BITGET_API_KEY",
    "BITGET_API_SECRET",
    "BITGET_API_PASSPHRASE",
    "SYMBOL",
    "TIMEFRAME",
    "DRY_RUN",
    "LEVERAGE",
    "MARGIN_USAGE_PCT",
    "SQUEEZE_THRESHOLD",
    "INITIAL_EQUITY",
    "POLL_INTERVAL",
    "DEEPSEEK_API_KEY",
]

DEFAULTS: dict[str, Any] = {
    "BITGET_API_KEY": "",
    "BITGET_API_SECRET": "",
    "BITGET_API_PASSPHRASE": "",
    "SYMBOL": "BTC/USDT:USDT",
    "TIMEFRAME": "15m",
    "DRY_RUN": True,
    "LEVERAGE": 5,
    "MARGIN_USAGE_PCT": 100.0,
    "SQUEEZE_THRESHOLD": 0.35,
    "INITIAL_EQUITY": 10_000.0,
    "POLL_INTERVAL": 60,
    "DEEPSEEK_API_KEY": "",
}

SENSITIVE_FIELDS = {
    "BITGET_API_KEY",
    "BITGET_API_SECRET",
    "BITGET_API_PASSPHRASE",
    "DEEPSEEK_API_KEY",
}

BOT_RUNTIME_FIELDS = {
    "BITGET_API_KEY",
    "BITGET_API_SECRET",
    "BITGET_API_PASSPHRASE",
    "SYMBOL",
    "TIMEFRAME",
    "DRY_RUN",
    "LEVERAGE",
    "MARGIN_USAGE_PCT",
    "SQUEEZE_THRESHOLD",
    "INITIAL_EQUITY",
    "POLL_INTERVAL",
}


class SettingsValidationError(ValueError):
    pass


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def _normalize_field(key: str, value: Any) -> Any:
    if key in {"BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE", "DEEPSEEK_API_KEY"}:
        return str(value or "").strip()
    if key in {"SYMBOL", "TIMEFRAME"}:
        return str(value or "").strip()
    if key == "DRY_RUN":
        return _parse_bool(value)
    if key in {"LEVERAGE", "POLL_INTERVAL"}:
        return int(value)
    if key in {"MARGIN_USAGE_PCT", "SQUEEZE_THRESHOLD", "INITIAL_EQUITY"}:
        return float(value)
    return value


def _serialize_field(key: str, value: Any) -> str:
    if key == "DRY_RUN":
        return "true" if bool(value) else "false"
    return str(value)


def _read_env_file(env_path: Path) -> tuple[dict[str, str], list[str]]:
    if not env_path.exists():
        return {}, []
    raw = dotenv_values(env_path)
    values = {
        key: ("" if value is None else str(value))
        for key, value in raw.items()
        if key
    }
    return values, env_path.read_text(encoding="utf-8").splitlines()


def load_runtime_settings(env_path: str | Path) -> dict[str, Any]:
    env_path = Path(env_path)
    raw_values, _ = _read_env_file(env_path)
    settings: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        settings[key] = _normalize_field(key, raw_values.get(key, default))
    return settings


def _validate_settings(values: dict[str, Any]) -> None:
    if not values["SYMBOL"]:
        raise SettingsValidationError("SYMBOL must not be empty")
    if not values["TIMEFRAME"]:
        raise SettingsValidationError("TIMEFRAME must not be empty")
    if values["LEVERAGE"] <= 0:
        raise SettingsValidationError("LEVERAGE must be a positive integer")
    if values["POLL_INTERVAL"] < 5:
        raise SettingsValidationError("POLL_INTERVAL must be >= 5")
    if values["INITIAL_EQUITY"] <= 0:
        raise SettingsValidationError("INITIAL_EQUITY must be > 0")
    if values["MARGIN_USAGE_PCT"] <= 0:
        raise SettingsValidationError("MARGIN_USAGE_PCT must be > 0")
    if values["SQUEEZE_THRESHOLD"] <= 0:
        raise SettingsValidationError("SQUEEZE_THRESHOLD must be > 0")


def save_settings(updates: dict[str, Any], env_path: str | Path) -> dict[str, Any]:
    env_path = Path(env_path)
    current, original_lines = _read_env_file(env_path)
    merged = load_runtime_settings(env_path)

    normalized_updates: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in DEFAULTS:
            continue
        normalized_updates[key] = _normalize_field(key, value)
        merged[key] = normalized_updates[key]

    _validate_settings(merged)

    merged_text = {key: _serialize_field(key, value) for key, value in merged.items()}
    output_lines: list[str] = []
    seen: set[str] = set()

    for line in original_lines:
        if "=" not in line or line.strip().startswith("#"):
            output_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in merged_text:
            output_lines.append(f"{key}={merged_text[key]}")
            seen.add(key)
        else:
            output_lines.append(line)

    for key in ENV_FIELD_ORDER:
        if key not in seen:
            output_lines.append(f"{key}={merged_text[key]}")
            seen.add(key)

    with NamedTemporaryFile("w", encoding="utf-8", dir=str(env_path.parent), delete=False) as tmp:
        tmp.write("\n".join(output_lines).rstrip() + "\n")
        tmp_path = Path(tmp.name)

    tmp_path.replace(env_path)

    return {
        "settings": merged,
        "updates": normalized_updates,
    }


def _secret_payload(value: str) -> dict[str, Any]:
    return {
        "is_set": bool(value),
        "value": _mask_secret(value),
    }


def load_settings_snapshot(env_path: str | Path, runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    settings = load_runtime_settings(env_path)
    runtime_state = runtime_state or {}
    return {
        "trading": {
            "bitget_api_key": _secret_payload(settings["BITGET_API_KEY"]),
            "bitget_api_secret": _secret_payload(settings["BITGET_API_SECRET"]),
            "bitget_api_passphrase": _secret_payload(settings["BITGET_API_PASSPHRASE"]),
            "symbol": settings["SYMBOL"],
            "timeframe": settings["TIMEFRAME"],
            "leverage": settings["LEVERAGE"],
            "dry_run": settings["DRY_RUN"],
            "poll_interval": settings["POLL_INTERVAL"],
            "initial_equity": settings["INITIAL_EQUITY"],
            "margin_usage_pct": settings["MARGIN_USAGE_PCT"],
            "squeeze_threshold": settings["SQUEEZE_THRESHOLD"],
        },
        "system": {
            "deepseek_api_key": _secret_payload(settings["DEEPSEEK_API_KEY"]),
        },
        "runtime": {
            "config_version": runtime_state.get("version", 0),
            "applied_version": runtime_state.get("applied_version", 0),
            "last_apply_error": runtime_state.get("last_apply_error", ""),
        },
    }


def test_settings_connections(payload: dict[str, Any]) -> dict[str, Any]:
    settings = {**DEFAULTS}
    for key, value in payload.items():
        if key in settings:
            settings[key] = _normalize_field(key, value)

    bitget_result = {"ok": False}
    if settings["BITGET_API_KEY"] and settings["BITGET_API_SECRET"] and settings["BITGET_API_PASSPHRASE"]:
        try:
            ex = ccxt.bitget(
                {
                    "apiKey": settings["BITGET_API_KEY"],
                    "secret": settings["BITGET_API_SECRET"],
                    "password": settings["BITGET_API_PASSPHRASE"],
                    "options": {"defaultType": "swap"},
                    "enableRateLimit": True,
                }
            )
            ex.fetch_balance({"type": "swap"})
            bitget_result = {"ok": True}
        except Exception as exc:
            bitget_result = {"ok": False, "error": str(exc)}
    else:
        bitget_result = {"ok": False, "error": "Missing Bitget credentials"}

    deepseek_result = {"ok": False}
    if settings["DEEPSEEK_API_KEY"]:
        try:
            client = OpenAI(
                api_key=settings["DEEPSEEK_API_KEY"],
                base_url="https://api.deepseek.com",
            )
            client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            deepseek_result = {"ok": True}
        except Exception as exc:
            deepseek_result = {"ok": False, "error": str(exc)}
    else:
        deepseek_result = {"ok": False, "error": "Missing DeepSeek API key"}

    return {
        "bitget": bitget_result,
        "deepseek": deepseek_result,
    }


test_settings_connections.__test__ = False
