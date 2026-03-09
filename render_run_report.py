#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any


def get(obj: dict[str, Any], *path: str, default: Any = None) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def extract_errors(verify_obj: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for section in ("lean_messages", "tool_messages"):
        errs = get(verify_obj, section, "errors", default=[])
        if isinstance(errs, list):
            out.extend(str(x).strip() for x in errs if str(x).strip())
    return out


def md_codeblock(text: str, lang: str = "") -> str:
    body = text.rstrip("\n")
    return f"```{lang}\n{body}\n```\n"


def truncate_lines(text: str, max_lines: int) -> str:
    lines = text.rstrip("\n").splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines]) + "\n...[truncated]"


def render_attempt(at: dict[str, Any], idx: int) -> str:
    lines: list[str] = []
    lines.append(f"### Attempt {idx}")
    lines.append("")
    lines.append(f"- `verify_ok`: `{at.get('verify_ok')}`")
    lines.append("")

    prompt = at.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        lines.append("#### Prompt")
        lines.append("")
        lines.append(md_codeblock(prompt, "text"))

    candidate = at.get("candidate")
    if isinstance(candidate, str) and candidate.strip():
        lines.append("#### Candidate Lean")
        lines.append("")
        lines.append(md_codeblock(candidate, "lean"))

    verify = at.get("verify", {})
    if isinstance(verify, dict):
        errs = extract_errors(verify)
        lines.append("#### Verify Errors")
        lines.append("")
        if errs:
            for e in errs:
                lines.append(f"- {e}")
        else:
            lines.append("- (none)")
        lines.append("")

    fallback_attempts = at.get("fallback_attempts", [])
    if isinstance(fallback_attempts, list) and fallback_attempts:
        lines.append("#### Fallback Attempts")
        lines.append("")
        for fb in fallback_attempts:
            tactic = fb.get("tactic", "?")
            ok = fb.get("verify_ok")
            lines.append(f"- tactic `{tactic}` -> `verify_ok={ok}`")
            fb_verify = fb.get("verify", {})
            if isinstance(fb_verify, dict):
                errs = extract_errors(fb_verify)
                if errs:
                    for e in errs:
                        lines.append(f"- {e}")
        lines.append("")

    if "repair_ok" in at:
        lines.append("#### Repair")
        lines.append("")
        lines.append(f"- `repair_ok`: `{at.get('repair_ok')}`")
        repair_verify = at.get("repair_verify", {})
        if isinstance(repair_verify, dict):
            errs = extract_errors(repair_verify)
            if errs:
                lines.append("- repair errors:")
                for e in errs:
                    lines.append(f"- {e}")
        lines.append("")

    return "\n".join(lines)


def render_result(res: dict[str, Any], ridx: int) -> str:
    lines: list[str] = []
    pid = res.get("problem_id", f"problem_{ridx}")
    lines.append(f"## Problem {ridx}: `{pid}`")
    lines.append("")
    lines.append(f"- status: `{res.get('status')}`")
    lines.append(f"- attempts_used: `{res.get('attempts_used')}`")
    if "solved_via" in res:
        lines.append(f"- solved_via: `{res.get('solved_via')}`")
    lines.append("")

    final_proof = res.get("final_proof")
    if isinstance(final_proof, str) and final_proof.strip():
        lines.append("### Final Proof")
        lines.append("")
        lines.append(md_codeblock(final_proof, "lean"))

    attempts = res.get("attempt_records", [])
    if isinstance(attempts, list):
        for i, at in enumerate(attempts, start=1):
            if isinstance(at, dict):
                lines.append(render_attempt(at, i))
                lines.append("")

    return "\n".join(lines)


def render_result_timeline(
    res: dict[str, Any], ridx: int, max_errors: int, prompt_lines: int
) -> str:
    lines: list[str] = []
    pid = res.get("problem_id", f"problem_{ridx}")
    lines.append(f"## Problem {ridx}: `{pid}`")
    lines.append("")
    lines.append(f"- status: `{res.get('status')}`")
    lines.append(f"- attempts_used: `{res.get('attempts_used')}`")
    if "solved_via" in res:
        lines.append(f"- solved_via: `{res.get('solved_via')}`")
    lines.append("")

    step = 1
    attempts = res.get("attempt_records", [])
    if isinstance(attempts, list):
        for i, at in enumerate(attempts, start=1):
            if not isinstance(at, dict):
                continue

            prompt = at.get("prompt")
            if isinstance(prompt, str) and prompt.strip():
                lines.append(f"### Step {step}: Attempt {i} Prompt")
                lines.append("")
                lines.append(md_codeblock(truncate_lines(prompt, prompt_lines), "text"))
                step += 1

            candidate = at.get("candidate")
            if isinstance(candidate, str) and candidate.strip():
                lines.append(f"### Step {step}: Attempt {i} Candidate")
                lines.append("")
                lines.append(md_codeblock(candidate, "lean"))
                step += 1

            verify_ok = at.get("verify_ok")
            verify = at.get("verify", {})
            lines.append(f"### Step {step}: Attempt {i} Verify (`ok={verify_ok}`)")
            lines.append("")
            if isinstance(verify, dict):
                errs = extract_errors(verify)
                if errs:
                    lines.append("Key errors:")
                    for e in errs[:max_errors]:
                        lines.append(f"- {e}")
                else:
                    lines.append("Key errors: none")
            else:
                lines.append("Key errors: unavailable")
            lines.append("")
            step += 1

            fallback_attempts = at.get("fallback_attempts", [])
            if isinstance(fallback_attempts, list):
                for fb in fallback_attempts:
                    if not isinstance(fb, dict):
                        continue
                    tactic = fb.get("tactic", "?")
                    fb_ok = fb.get("verify_ok")
                    lines.append(
                        f"### Step {step}: Attempt {i} Fallback `{tactic}` (`ok={fb_ok}`)"
                    )
                    lines.append("")
                    fb_verify = fb.get("verify", {})
                    if isinstance(fb_verify, dict):
                        errs = extract_errors(fb_verify)
                        if errs:
                            lines.append("Key errors:")
                            for e in errs[:max_errors]:
                                lines.append(f"- {e}")
                        else:
                            lines.append("Key errors: none")
                    else:
                        lines.append("Key errors: unavailable")
                    lines.append("")
                    step += 1

            if "repair_ok" in at:
                repair_ok = at.get("repair_ok")
                lines.append(f"### Step {step}: Attempt {i} Repair (`ok={repair_ok}`)")
                lines.append("")
                repair_verify = at.get("repair_verify", {})
                if isinstance(repair_verify, dict):
                    errs = extract_errors(repair_verify)
                    if errs:
                        lines.append("Key errors:")
                        for e in errs[:max_errors]:
                            lines.append(f"- {e}")
                    else:
                        lines.append("Key errors: none")
                else:
                    lines.append("Key errors: unavailable")
                lines.append("")
                step += 1

    final_proof = res.get("final_proof")
    if isinstance(final_proof, str) and final_proof.strip():
        lines.append(f"### Step {step}: Final Proof")
        lines.append("")
        lines.append(md_codeblock(final_proof, "lean"))
    else:
        lines.append(f"### Step {step}: Final Result")
        lines.append("")
        lines.append("- no final proof (failed)")

    return "\n".join(lines)


def render_report(
    data: dict[str, Any],
    src: Path,
    style: str,
    max_errors: int,
    prompt_lines: int,
) -> str:
    lines: list[str] = []
    lines.append("# AutoProve Run Report")
    lines.append("")
    lines.append(f"- style: `{style}`")
    lines.append(f"- source_json: `{src}`")
    lines.append(f"- run_id: `{data.get('run_id')}`")
    lines.append(f"- provider: `{data.get('llm_provider')}`")
    lines.append(f"- model: `{data.get('model')}`")
    lines.append(f"- solved: `{data.get('solved')}/{data.get('total')}`")
    lines.append("")

    results = data.get("results", [])
    if isinstance(results, list):
        for i, res in enumerate(results, start=1):
            if isinstance(res, dict):
                if style == "timeline":
                    lines.append(render_result_timeline(res, i, max_errors, prompt_lines))
                else:
                    lines.append(render_result(res, i))
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render autoprove run JSON to markdown report.")
    parser.add_argument("input_json", help="Path to run-*.json")
    parser.add_argument("-o", "--output", default=None, help="Output markdown path")
    parser.add_argument(
        "--style",
        choices=["full", "timeline"],
        default="timeline",
        help="Report style. `timeline` shows step-by-step progression.",
    )
    parser.add_argument(
        "--max-errors",
        type=int,
        default=3,
        help="Max errors to show per verify/repair/fallback block.",
    )
    parser.add_argument(
        "--prompt-lines",
        type=int,
        default=50,
        help="Max prompt lines to include in timeline mode.",
    )
    args = parser.parse_args()

    src = Path(args.input_json)
    if not src.exists():
        raise SystemExit(f"Input file not found: {src}")

    data = json.loads(src.read_text(encoding="utf-8"))
    report = render_report(
        data=data,
        src=src.resolve(),
        style=args.style,
        max_errors=args.max_errors,
        prompt_lines=args.prompt_lines,
    )

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = src.with_suffix(".md")
    out_path.write_text(report, encoding="utf-8")
    print(f"Wrote report: {out_path}")


if __name__ == "__main__":
    main()
