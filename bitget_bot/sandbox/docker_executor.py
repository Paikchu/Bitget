"""
Docker sandbox executor — primary security boundary.
Spawns a temporary container per backtest request, communicates via stdin/stdout.
Container config: no network, no file writes, 512MB RAM, 1 CPU, 120s timeout.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

from bitget_bot.sandbox.ast_validator import validate_strategy_code

log = logging.getLogger(__name__)

SANDBOX_IMAGE = "strategy-sandbox:latest"
_DOCKER_CANDIDATE_PATHS = (
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/usr/bin/docker",
    "/bin/docker",
)


class SandboxError(Exception):
    """Raised when sandbox execution fails (includes code check failures)."""


def _docker_available() -> bool:
    return _resolve_docker_bin() is not None


def _resolve_docker_bin() -> str | None:
    env_bin = os.environ.get("DOCKER_BIN")
    if env_bin and os.path.exists(env_bin):
        return env_bin

    detected = shutil.which("docker")
    if detected:
        return detected

    for candidate in _DOCKER_CANDIDATE_PATHS:
        if os.path.exists(candidate):
            return candidate
    return None


def _image_exists(docker_bin: str) -> bool:
    result = subprocess.run(
        [docker_bin, "image", "inspect", SANDBOX_IMAGE],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def _parse_sandbox_result(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    log.debug("Sandbox exit=%d stderr=%s", returncode, stderr[:200] if stderr else "")

    if returncode != 0:
        raise SandboxError(f"策略执行错误: {stderr[:500]}")

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        raise SandboxError(f"沙箱输出格式错误: {stdout[:200]}")

    if not result.get("success"):
        raise SandboxError(result.get("error", "未知错误"))

    return result


def _run_with_docker_cli(payload: bytes, timeout: int, docker_bin: str) -> dict[str, Any]:
    cmd = [
        docker_bin, "run",
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
    except Exception as exc:
        raise SandboxError(f"容器启动失败: {exc}")

    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    stderr = proc.stderr.decode("utf-8", errors="replace").strip()
    return _parse_sandbox_result(stdout, stderr, proc.returncode)


def _run_with_docker_sdk(
    strategy_code: str,
    ohlcv: list,
    params: dict,
    timeout: int,
) -> dict[str, Any]:
    try:
        import docker
        from docker.errors import DockerException, ImageNotFound
    except Exception as exc:
        raise SandboxError(f"Docker CLI 不可用，且 Docker SDK 不可用: {exc}")

    payload = {
        "strategy_code": strategy_code,
        "ohlcv": ohlcv,
        "params": params,
    }
    payload_bytes = json.dumps(payload).encode("utf-8")

    client = None
    api_client = None
    container = None
    stdin_sock = None
    try:
        client = docker.from_env()
        client.ping()
        api_client = docker.APIClient()
        try:
            client.images.get(SANDBOX_IMAGE)
        except ImageNotFound:
            raise SandboxError(
                f"Sandbox image '{SANDBOX_IMAGE}' not found. "
                f"Run: docker build -f sandbox/Dockerfile.sandbox -t {SANDBOX_IMAGE} sandbox/"
            )

        host_config = api_client.create_host_config(
            network_mode="none",
            read_only=True,
            mem_limit="512m",
            memswap_limit="512m",
            nano_cpus=1_000_000_000,
            pids_limit=64,
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
            tmpfs={"/tmp": "size=64m,noexec,nodev"},
        )
        container = api_client.create_container(
            SANDBOX_IMAGE,
            host_config=host_config,
            stdin_open=True,
            tty=False,
        )
        stdin_sock = api_client.attach_socket(
            container,
            params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1},
        )
        api_client.start(container)
        raw_sock = getattr(stdin_sock, "_sock", stdin_sock)
        raw_sock.sendall(payload_bytes)
        raw_sock.close()
        stdin_sock.close()
        stdin_sock = None
        status = api_client.wait(container, timeout=timeout)
        stdout = api_client.logs(container, stdout=True, stderr=False).decode("utf-8", errors="replace").strip()
        stderr = api_client.logs(container, stdout=False, stderr=True).decode("utf-8", errors="replace").strip()
    except SandboxError:
        raise
    except DockerException as exc:
        raise SandboxError(f"Docker daemon 不可用: {exc}")
    except Exception as exc:
        raise SandboxError(f"容器启动失败: {exc}")
    finally:
        if stdin_sock is not None:
            try:
                stdin_sock.close()
            except Exception:
                pass
        if container is not None:
            try:
                target = container.get("Id", container)
                if api_client is not None:
                    api_client.remove_container(target, force=True)
            except Exception:
                pass
        if api_client is not None:
            try:
                api_client.close()
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    return _parse_sandbox_result(stdout, stderr, int(status.get("StatusCode", 1)))


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

    docker_bin = _resolve_docker_bin()
    if docker_bin:
        if not _image_exists(docker_bin):
            raise SandboxError(
                f"Sandbox image '{SANDBOX_IMAGE}' not found. "
                f"Run: docker build -f sandbox/Dockerfile.sandbox -t {SANDBOX_IMAGE} sandbox/"
            )
        payload = json.dumps({
            "strategy_code": strategy_code,
            "ohlcv": ohlcv,
            "params": params,
        }).encode()
        return _run_with_docker_cli(payload, timeout, docker_bin)

    log.warning("Docker CLI not found on PATH; falling back to Docker SDK")
    return _run_with_docker_sdk(strategy_code, ohlcv, params, timeout)
