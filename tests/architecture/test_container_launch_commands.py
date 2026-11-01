"""Every container command that starts uvicorn must bound its shutdown.

A browser tab holds an open WebSocket, and uvicorn's graceful shutdown waits
for every connection to drain before the reloader (or `docker stop`) can
finish. With uvicorn's default — no graceful-shutdown timeout — the backend
sits in "Waiting for connections to close. (CTRL+C to force quit)" until it is
killed, so `uvicorn --reload` stops picking up changes and a container restart
hangs. `deeptutor/api/run_server.py` already bounds it; these are the commands
launched directly from a shell, which must say so explicitly.
"""

from __future__ import annotations

from pathlib import Path
import re

def _repo_root() -> Path:
    """Nearest ancestor that still holds the Dockerfile this test inspects."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "Dockerfile").is_file():
            return candidate
    raise AssertionError("Dockerfile not found above this test")


ROOT = _repo_root()

#: One launch of the app; the flag has to appear on the same command.
_UVICORN_APP = re.compile(r"uvicorn\s+deeptutor\.api\.main:app")


def _launch_commands() -> list[tuple[str, str]]:
    """Every (file, command line) that launches the backend from a shell."""
    commands: list[tuple[str, str]] = []
    dockerfile = ROOT / "Dockerfile"
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        if _UVICORN_APP.search(line) and not line.strip().startswith("#"):
            commands.append((str(dockerfile.relative_to(ROOT)), line.strip()))
    return commands


def test_container_uvicorn_commands_bound_their_graceful_shutdown() -> None:
    commands = _launch_commands()

    assert commands, "expected at least one uvicorn launch in the Dockerfile"
    missing = [
        f"{path}: {line}"
        for path, line in commands
        if "--timeout-graceful-shutdown" not in line
    ]
    assert not missing, (
        "uvicorn launched without --timeout-graceful-shutdown can hang forever "
        "on an open browser WebSocket (reload stops, docker stop hangs):\n"
        + "\n".join(missing)
    )
