#!/usr/bin/env python3
"""
Interactive single-problem runner for autoprove.py.

Flow:
1) Read a Lean formal statement (from file/stdin/interactive paste)
2) Build a temporary one-problem YAML
3) Run autoprove.py with verbose attempts
4) Stream output to terminal and outputs/live/live-*.log (plus live-latest symlink)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run one Lean theorem through LLM -> AXLE -> Lean loop with live logs."
    )
    p.add_argument("--formal-file", default=None, help="Path to a Lean statement file.")
    p.add_argument("--problem-id", default="interactive_problem", help="Problem id.")
    p.add_argument("--hint", default="", help="Optional problem hint.")
    p.add_argument("--environment", default="lean-4.28.0", help="AXLE environment.")
    p.add_argument(
        "--llm-provider",
        choices=["auto", "openrouter", "openai"],
        default="openrouter",
        help="LLM provider to use.",
    )
    p.add_argument("--model", default=None, help="Optional model override.")
    p.add_argument("--max-attempts", type=int, default=8, help="Max attempts.")
    p.add_argument(
        "--no-repair",
        action="store_true",
        help="Disable AXLE repair_proofs step.",
    )
    p.add_argument(
        "--fallback-tactics",
        default="omega,nlinarith",
        help="Fallback tactics CSV (pass empty string to disable).",
    )
    p.add_argument(
        "--fallback-after-attempt",
        type=int,
        default=1,
        help="Start fallback from this attempt number.",
    )
    p.add_argument("--output-dir", default="outputs/interactive", help="Run JSON output directory.")
    p.add_argument("--live-dir", default="outputs/live", help="Live log directory.")
    return p.parse_args()


def read_formal_statement(args: argparse.Namespace) -> str:
    if args.formal_file:
        p = Path(args.formal_file)
        if not p.exists():
            raise SystemExit(f"formal file not found: {p}")
        return p.read_text(encoding="utf-8").rstrip() + "\n"

    if not sys.stdin.isatty():
        text = sys.stdin.read()
        if text.strip():
            return text.rstrip() + "\n"

    print("Lean formal statement를 붙여넣고 마지막 줄에 :::END 를 입력하세요.")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == ":::END":
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        raise SystemExit("No formal statement provided.")
    return text + "\n"


def write_problem_yaml(
    formal_statement: str,
    args: argparse.Namespace,
    ts: str,
    output_dir: Path,
) -> Path:
    problem_obj = {
        "problems": [
            {
                "id": args.problem_id,
                "environment": args.environment,
                "hint": args.hint,
                "formal_statement": formal_statement,
            }
        ]
    }
    yml_path = output_dir / f"problem-{ts}.yaml"
    yml_path.write_text(yaml.safe_dump(problem_obj, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return yml_path


def build_command(args: argparse.Namespace, problems_path: Path) -> list[str]:
    cmd = [
        sys.executable,
        "autoprove.py",
        "--problems",
        str(problems_path),
        "--llm-provider",
        args.llm_provider,
        "--max-attempts",
        str(args.max_attempts),
        "--verbose-attempts",
        "--output-dir",
        args.output_dir,
        "--fallback-tactics",
        args.fallback_tactics,
        "--fallback-after-attempt",
        str(args.fallback_after_attempt),
    ]
    if args.model:
        cmd += ["--model", args.model]
    if not args.no_repair:
        cmd += ["--use-repair"]
    return cmd


def run_and_tee(cmd: list[str], log_path: Path, env: dict[str, str]) -> tuple[int, str]:
    summary_path = ""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    assert proc.stdout is not None

    with log_path.open("w", encoding="utf-8") as lf:
        for line in proc.stdout:
            sys.stdout.write(line)
            lf.write(line)
            m = re.search(r"Summary:\s+(.+)\s*$", line)
            if m:
                summary_path = m.group(1).strip()

    code = proc.wait()
    return code, summary_path


def main() -> None:
    args = parse_args()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir)
    live_dir = Path(args.live_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    live_dir.mkdir(parents=True, exist_ok=True)

    if not os.getenv("AXLE_API_KEY"):
        raise SystemExit("AXLE_API_KEY is required.")
    if args.llm_provider == "openrouter" and not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is required for --llm-provider openrouter.")
    if args.llm_provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required for --llm-provider openai.")

    formal_statement = read_formal_statement(args)
    if "sorry" not in formal_statement:
        print(
            "[warn] formal statement에 `sorry`가 없습니다. LLM이 전체 파일을 다시 생성하도록 시도합니다.",
            flush=True,
        )

    problems_path = write_problem_yaml(formal_statement, args, ts, output_dir)
    log_path = live_dir / f"live-{ts}.log"
    latest_link = live_dir / "live-latest.log"

    try:
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        latest_link.symlink_to(log_path.name)
    except OSError:
        # Symlink creation might fail on some environments. Not fatal.
        pass

    cmd = build_command(args, problems_path)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    print(f"[interactive] problem yaml: {problems_path}")
    print(f"[interactive] live log:     {log_path}")
    if latest_link.exists() or latest_link.is_symlink():
        print(f"[interactive] live latest:  {latest_link}")
    print(f"[interactive] cmd: {' '.join(cmd)}")
    print()

    code, summary_path = run_and_tee(cmd, log_path, env)

    print()
    print(f"[interactive] exit code: {code}")
    print(f"[interactive] log saved: {log_path}")
    if summary_path:
        print(f"[interactive] summary:   {summary_path}")
    if code != 0:
        raise SystemExit(code)


if __name__ == "__main__":
    main()

