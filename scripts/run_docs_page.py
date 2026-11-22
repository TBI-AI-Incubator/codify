"""Run a docs page's `bash` blocks in order (`bash background` stays up; `python title=` is saved).
The next other block is the expected output: its lines appear in order; `...`, UUIDs, dates,
times and whitespace runs are loose, and a UUID seen in output is substituted into later commands.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

FENCE = re.compile(r"^```(\w+)([^\n]*)\n(.*?)^```$", re.M | re.S)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TOKEN = re.compile(rf"( ?\.\.\. ?)|({UUID.pattern})|\d{{4}}-\d\d-\d\d|\d\d:\d\d:\d\d")
MODEL_ENV = ("LITELLM_", "EMBEDDING_", "OPENAI_", "GEMINI_", "ANTHROPIC_", "AZURE_", "LLM_")


def _norm(line: str) -> str:
    return " ".join(line.split())


def _line_pattern(expected: str, bound: dict[str, str]) -> tuple[re.Pattern[str], dict[str, str]]:
    parts: list[str] = []
    groups: dict[str, str] = {}
    pos = 0
    for m in TOKEN.finditer(expected):
        parts.append(re.escape(expected[pos : m.start()]))
        ellipsis, uuid = m.groups()
        if ellipsis:
            parts.append(".*?")
        elif uuid in bound:
            parts.append(re.escape(bound[uuid]))
        elif uuid in groups:
            parts.append(f"(?P={groups[uuid]})")
        elif uuid:
            groups[uuid] = f"u{len(groups)}"
            parts.append(f"(?P<{groups[uuid]}>{UUID.pattern})")
        else:  # a date or a time
            parts.append(re.sub(r"\d", r"\\d", m.group()))
        pos = m.end()
    parts.append(re.escape(expected[pos:]))
    return re.compile("".join(parts)), groups


def match(expected: str, actual: str, bound: dict[str, str]) -> str | None:
    """Bind page UUIDs into `bound`; return the first expected line not found, or None."""
    lines = [_norm(a) for a in actual.splitlines()]
    i = 0
    for raw in expected.splitlines():
        want = _norm(raw)
        if want in ("", "..."):
            continue
        pattern, groups = _line_pattern(want, bound)
        m = None
        while i < len(lines) and not (m := pattern.fullmatch(lines[i])):
            i += 1
        if m is None:
            return raw
        bound.update({uuid: m.group(name) for uuid, name in groups.items()})
        i += 1
    return None


def _self_check() -> None:
    bound: dict[str, str] = {}
    page = 'start\n{"id": "d149f6c8-c598-4511-98b5-251c5b06de87", "n": 1, ...}\n...\nend'
    real = 'log\nstart\n{"id": "0a49f6c8-c598-4511-98b5-251c5b06de87", "n": 1, "x": 2}\nmore\nend'
    assert match(page, real, bound) is None
    assert bound == {"d149f6c8-c598-4511-98b5-251c5b06de87": "0a49f6c8-c598-4511-98b5-251c5b06de87"}
    assert match('"n": 2', real, {}) == '"n": 2'
    assert match("end\nstart", real, {}) == "start"


def _wait_for_port(port: int, proc: subprocess.Popen[bytes]) -> None:
    for _ in range(120):
        if proc.poll() is not None:
            sys.exit(f"background command exited with {proc.returncode}")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    sys.exit(f"nothing listened on port {port}")


def run(page: Path, port: int) -> int:
    env = {k: v for k, v in os.environ.items() if not k.startswith(MODEL_ENV)}
    bound: dict[str, str] = {}
    background: list[subprocess.Popen[bytes]] = []
    pending: tuple[str, str] | None = None  # (command, its output)
    ran = 0
    try:
        for m in FENCE.finditer(page.read_text(encoding="utf-8")):
            lang, meta, body = m.group(1), m.group(2), m.group(3)
            if lang == "bash":
                for page_id, real_id in bound.items():
                    body = body.replace(page_id, real_id)
                body = body.replace("localhost:8000", f"localhost:{port}")
                ran += 1
                if "background" in meta.split():
                    cmd = f"{body.strip()} --port {port}"
                    print(f"--- background: {cmd}", flush=True)
                    proc = subprocess.Popen(["bash", "-c", cmd], env=env, start_new_session=True)
                    background.append(proc)
                    _wait_for_port(port, proc)
                    pending = None
                    continue
                print(f"--- {body.strip()}", flush=True)
                done = subprocess.run(
                    ["bash", "-eo", "pipefail", "-c", body],
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=600,
                )
                if done.returncode:
                    print(done.stdout)
                    sys.exit(f"exit code {done.returncode}")
                pending = (body.strip(), done.stdout)
            elif lang == "python" and (title := re.search(r'title="([^"]+)"', meta)):
                Path(title.group(1)).write_text(body, encoding="utf-8")
                print(f"--- wrote {title.group(1)}", flush=True)
                pending = None
            elif pending:
                missing = match(body, pending[1], bound)
                if missing is not None:
                    print(pending[1])
                    sys.exit(f"output of `{pending[0]}` lacks the page's line:\n  {missing}")
                pending = None
    finally:
        for proc in background:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=30)
    print(f"--- {page}: {ran} commands ran and matched the page")
    return 0


if __name__ == "__main__":
    _self_check()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("page", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    sys.exit(run(args.page, args.port))
