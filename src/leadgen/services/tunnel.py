"""`leadgen serve --public`: the MCP server plus a Cloudflare quick tunnel, for ChatGPT.

Two child processes are started and stopped together: `leadgen mcp --http` on 127.0.0.1 and
`cloudflared tunnel --url ...`. The public address contains the secret path, which is the only
protection, so it is printed once and never logged.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from leadgen.core import config

QUICK_URL_RE = re.compile(r"https://([a-z0-9][a-z0-9-]*)\.trycloudflare\.com\b", re.IGNORECASE)
NOT_A_TUNNEL = {"api"}  # error lines mention https://api.trycloudflare.com/tunnel
TUNNEL_WAIT_S = 45.0
SERVER_WAIT_S = 25.0
DEFAULT_PORT = 8766
WARNING = (
    "WARNING: anyone who has this address can use your Lead Generator, read your leads and "
    "spend your credits, for as long as this window stays open. Do not share it. "
    "Closing this window (or pressing Ctrl+C) switches it off. "
    "A new address is made every time (use --rotate-secret to also change the secret)."
)


def _say(text: str) -> None:
    print(text, flush=True)


class TunnelError(Exception):
    """The public connection could not be started. The message says what to do."""


# --- cloudflared ------------------------------------------------------------------------------


def install_hint(platform: str | None = None) -> str:
    """The command that installs cloudflared on this system."""
    platform = platform or sys.platform
    if platform == "win32":
        return "winget install --id Cloudflare.cloudflared"
    if platform == "darwin":
        return "brew install cloudflared"
    return (
        "download it from https://developers.cloudflare.com/cloudflare-one/networks/connectors/"
        "cloudflare-tunnel/downloads/ and put it on your PATH"
    )


def _common_dirs(env: dict[str, str], platform: str) -> list[Path]:
    home = Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())
    if platform == "win32":
        dirs = [
            Path(env.get("ProgramFiles", "C:/Program Files")) / "cloudflared",
            Path(env.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "cloudflared",
        ]
        if env.get("LOCALAPPDATA"):
            dirs.append(Path(env["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links")
        dirs.append(home / "scoop" / "shims")
        return dirs
    return [
        Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/usr/bin"),
        home / ".local" / "bin", home / "bin",
    ]  # fmt: skip


def find_cloudflared(
    which: Callable[[str], str | None] = shutil.which,
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> str | None:
    """Path to cloudflared: on PATH first, then the usual install folders. None if not found."""
    found = which("cloudflared")
    if found:
        return found
    platform = platform or sys.platform
    name = "cloudflared.exe" if platform == "win32" else "cloudflared"
    for folder in _common_dirs(dict(os.environ) if env is None else env, platform):
        candidate = folder / name
        if candidate.is_file():
            return str(candidate)
    return None


def parse_tunnel_url(line: str) -> str | None:
    """The `https://<random>.trycloudflare.com` address in one line of cloudflared output."""
    for match in QUICK_URL_RE.finditer(line):
        if match.group(1).lower() not in NOT_A_TUNNEL:
            return f"https://{match.group(1).lower()}.trycloudflare.com"
    return None


def find_tunnel_url(lines: Iterable[str]) -> str | None:
    """The first quick-tunnel address in a block of cloudflared output, or None."""
    for line in lines:
        url = parse_tunnel_url(line)
        if url:
            return url
    return None


def public_address(tunnel_url: str, secret: str) -> str:
    """The address to paste into ChatGPT."""
    return f"{tunnel_url.rstrip('/')}/mcp/{secret}"


def tunnel_command(cloudflared: str, port: int) -> list[str]:
    """cloudflared's arguments. The Host header is set to localhost so the local server's
    DNS-rebinding guard accepts tunnel traffic without opening it to other names."""
    return [
        cloudflared, "tunnel", "--no-autoupdate",
        "--http-host-header", f"localhost:{port}",
        "--url", f"http://127.0.0.1:{port}",
    ]  # fmt: skip


def server_command(port: int) -> list[str]:
    """The local MCP server (Streamable HTTP) as a child process."""
    return [sys.executable, "-m", "leadgen", "mcp", "--http", "--port", str(port)]


# --- processes --------------------------------------------------------------------------------


def _popen(cmd: list[str]) -> subprocess.Popen[str]:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )


class _Reader:
    """Collects a child's output lines in a queue (and the last few for error messages)."""

    def __init__(self, proc: subprocess.Popen[str]):
        self.proc = proc
        self.lines: queue.Queue[str] = queue.Queue()
        self.tail: list[str] = []
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        try:
            for raw in self.proc.stdout:
                line = raw.rstrip()
                self.tail = (self.tail + [line])[-12:]
                self.lines.put(line)
        except (OSError, ValueError):
            pass

    def alive(self) -> bool:
        return self.proc.poll() is None

    def recent(self) -> str:
        """Last output lines with any secret address removed."""
        return "\n".join(line for line in self.tail if "/mcp/" not in line)


def stop(proc: subprocess.Popen[str] | None) -> None:
    """End a child process: ask politely, then force."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    except OSError:
        pass


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """True when something already listens on host:port."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def instructions(address: str) -> str:
    """Step-by-step ChatGPT instructions with the address filled in (menu names: DECISIONS T17)."""
    return f"""\
Add it to ChatGPT (on a computer, in your web browser):
  1. Open https://chatgpt.com/plugins and click the + (plus) button.
  2. Choose "Add custom MCP server".
  3. Name: Lead Generator. Paste this address as the server URL:

       {address}

  4. Authentication: choose "No authentication" (the secret is part of the address).
  5. Tick "I understand and want to continue", then click "Create as a plugin".
  6. Open a new chat, type @ and pick Lead Generator, then ask:
     "What is my Lead Generator status?"

If you cannot find these buttons, your ChatGPT plan or workspace may need Developer Mode
turned on first (Settings, then Connectors/Apps, then Advanced). On Business and Enterprise
plans a workspace admin may have to allow custom connectors. See docs/user/CONNECT_CHATGPT.md.

{WARNING}"""


def run_public(
    *,
    port: int | None = None,
    rotate_secret: bool = False,
    out: Callable[[str], None] = _say,
    wait: Callable[[], None] | None = None,
) -> int:
    """Start the server and the tunnel, print the address, and run until Ctrl+C.

    `wait` replaces the "sleep until a child dies or Ctrl+C" loop (used by tests).
    Returns a process exit code.
    """
    cloudflared = find_cloudflared()
    if not cloudflared:
        out("cloudflared is not installed. It makes the secure link ChatGPT needs.")
        out(f"Install it with:  {install_hint()}")
        out("Then run this command again.")
        return 1
    secret = config.rotate_mcp_secret() if rotate_secret else config.ensure_mcp_secret()
    if rotate_secret:
        out("A new secret was made. Any address you used before no longer works.")
    port = port or config.load_settings().mcp_http.port or DEFAULT_PORT
    if port_in_use(port):
        out(f"Port {port} is already in use by another program. Try again with --port 8777.")
        return 1

    server: subprocess.Popen[str] | None = None
    tunnel: subprocess.Popen[str] | None = None
    try:
        out("Starting the Lead Generator server...")
        server = _popen(server_command(port))
        server_out = _Reader(server)
        deadline = time.monotonic() + SERVER_WAIT_S
        while not port_in_use(port):
            if not server_out.alive() or time.monotonic() > deadline:
                raise TunnelError(
                    "The server did not start.\n" + server_out.recent()
                    + "\nRun `leadgen doctor` to check the installation."
                )
            time.sleep(0.25)
        out("Starting the secure link (this takes a few seconds)...")
        tunnel = _popen(tunnel_command(cloudflared, port))
        tunnel_out = _Reader(tunnel)
        url = _wait_for_url(tunnel_out)
        out("")
        out(instructions(public_address(url, secret)))
        out("")
        out("Running. Press Ctrl+C to stop.")
        (wait or (lambda: _wait_until_stopped(server_out, tunnel_out)))()
        code = 0
        if not server_out.alive() or not tunnel_out.alive():
            out("One of the two programs stopped, so everything is being switched off.")
            code = 1
        return code
    except TunnelError as exc:
        out(str(exc))
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        out("Switching off...")
        stop(tunnel)
        stop(server)
        out("Stopped. The address no longer works.")


def _wait_for_url(reader: _Reader) -> str:
    deadline = time.monotonic() + TUNNEL_WAIT_S
    while time.monotonic() < deadline:
        try:
            line = reader.lines.get(timeout=0.5)
        except queue.Empty:
            if not reader.alive():
                break
            continue
        url = parse_tunnel_url(line)
        if url:
            return url
    raise TunnelError(
        "The secure link did not start. Check your internet connection, and that a "
        "firewall or antivirus is not blocking cloudflared. Last messages:\n" + reader.recent()
    )


def _wait_until_stopped(*readers: _Reader) -> None:
    while all(r.alive() for r in readers):
        time.sleep(0.5)


def connect_chatgpt(out: Callable[[str], None] = _say, **kwargs: Any) -> int:
    """`leadgen connect chatgpt`: same as `leadgen serve --public`."""
    return run_public(out=out, **kwargs)
