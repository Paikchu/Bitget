"""
Docker sandbox executor — primary security boundary.
Spawns a temporary container per backtest request, communicates via stdin/stdout.
Container config: no network, no file writes, 512MB RAM, 1 CPU, 120s timeout.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

from bitget_bot.sandbox.ast_validator import validate_strategy_code

log = logging.getLogger(__name__)

SANDBOX_IMAGE = "strategy-sandbox:latest"


class SandboxError(Exception):
    """Raised when sandbox execution fails (includes code check failures)."""


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _image_exists() -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", SANDBOX_IMAGE],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def run_strategy_in_sandbox(
    strategy_code: str,
    ohlcv: list,
    params: dict,
    timeout: int = 120,
) -> dict[str, Any]:
    """
    Run strategy backtest in Docker sandbox.

    Args:
        strategy_code: Python strategy code string
        ohlcv: list of [timestamp_ms, open, high, low, close, volume]
        params: backtest params dict
        timeout: max wait seconds

    Returns:
        Result dict with summary, equity_curve, trades

    Raises:
        SandboxError: code check failure or runtime error
    """
    # Layer 1: AST static check
    errors = validate_strategy_code(strategy_code)
    if errors:
        raise SandboxError(
            "代码安全检查未通过:\n" + "\n".join(f"  • {e}" for e in errors)
        )

    if not _docker_available():
        raise SandboxError("Docker CLI not found. Install Docker and ensure it is on PATH.")

    if not _image_exists():
        raise SandboxError(
            f"Sandbox image '{SANDBOX_IMAGE}' not found. "
            f"Run: docker build -f sandbox/Dockerfile.sandbox -t {SANDBOX_IMAGE} sandbox/"
        )

    payload = json.dumps({
        "strategy_code": strategy_code,
        "ohlcv": ohlcv,
        "params": params,
    }).encode()

    cmd = [
        "docker", "run",
        "--rm",
        "--interactive",
        "--network", "none",
        "--security-opt", "no-new-privileges:true",
        "--cap-drop", "ALL",
        "--read-only",
        "--memory", "512m",
        "--memory-swap", "512m",
        "--cpus", "1",
        "--pids-limit", "64",
        "--tmpfs", "/tmp:size=64m,noexec,nodev",
        SANDBOX_IMAGE,
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise SandboxError(f"回测执行超时（>{timeout}s），已强制终止容器")
    except Exception as e:
        raise SandboxError(f"容器启动失败: {e}")

    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()

    log.debug("Sandbox exit=%d stderr=%s", proc.returncode, stderr[:200] if stderr else "")

    if proc.returncode != 0:
        raise SandboxError(f"策略执行错误: {stderr[:500]}")

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        raise SandboxError(f"沙箱输出格式错误: {stdout[:200]}")

    if not result.get("success"):
        raise SandboxError(result.get("error", "未知错误"))

    return result
