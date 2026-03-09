#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import re
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from openai import AsyncOpenAI

try:
    from axle import AxleClient
except ImportError as exc:
    raise SystemExit(
        "Failed to import AxleClient. Install dependencies with: pip install -r requirements.txt"
    ) from exc


SYSTEM_PROMPT = """You are a Lean 4 theorem proving assistant.
Return only Lean code as plain text.
Do not include markdown fences.
Do not explain anything.
Return the full Lean source for the current formal statement.
Keep the existing declarations and replace each `sorry` with valid proofs.
If helper defs/lemmas are present, keep them so the file compiles as a whole.
Prefer short, robust proofs.
"""


@dataclass
class Problem:
    problem_id: str
    formal_statement: str
    environment: str | None = None
    tags: list[str] = field(default_factory=list)
    hint: str = ""


def parse_csv(arg: str | None) -> list[str]:
    if not arg:
        return []
    return [x.strip() for x in arg.split(",") if x.strip()]


def load_problems(path: Path) -> list[Problem]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        records = raw.get("problems", [])
    elif isinstance(raw, list):
        records = raw
    else:
        raise ValueError("Problems file must be a list or a dict with `problems`.")

    problems: list[Problem] = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise ValueError(f"Problem index {i} is not a mapping.")
        formal_statement = rec.get("formal_statement")
        if not formal_statement:
            raise ValueError(f"Problem index {i} missing `formal_statement`.")
        pid = rec.get("id") or rec.get("problem_id") or f"problem_{i+1}"
        problems.append(
            Problem(
                problem_id=str(pid),
                formal_statement=str(formal_statement).rstrip() + "\n",
                environment=rec.get("environment"),
                tags=list(rec.get("tags", [])),
                hint=str(rec.get("hint", "")).strip(),
            )
        )
    return problems


def to_dict(obj: Any) -> dict[str, Any]:
    def json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [json_safe(v) for v in value]
        if isinstance(value, tuple):
            return [json_safe(v) for v in value]
        if isinstance(value, dict):
            return {str(k): json_safe(v) for k, v in value.items()}
        if hasattr(value, "model_dump"):
            try:
                return json_safe(value.model_dump())
            except Exception:
                pass
        if hasattr(value, "dict"):
            try:
                return json_safe(value.dict())
            except Exception:
                pass
        if hasattr(value, "__dict__"):
            try:
                return {
                    str(k): json_safe(v)
                    for k, v in vars(value).items()
                    if not str(k).startswith("_")
                }
            except Exception:
                pass
        return str(value)

    converted = json_safe(obj)
    if isinstance(converted, dict):
        return converted
    return {"value": converted}


def get_okay(obj: Any) -> bool:
    d = to_dict(obj)
    return bool(d.get("okay", False))


def get_content(obj: Any) -> str:
    d = to_dict(obj)
    content = d.get("content")
    if isinstance(content, str):
        return content
    return ""


def stringify_error_item(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        text = item.get("message") or item.get("error") or item.get("text")
        if text:
            return str(text)
        return json.dumps(item, ensure_ascii=False)
    return str(item)


def extract_errors(obj: Any) -> list[str]:
    d = to_dict(obj)
    errors: list[str] = []

    for key in ("errors",):
        items = d.get(key, [])
        if isinstance(items, list):
            errors.extend(stringify_error_item(x) for x in items)
        elif items:
            errors.append(stringify_error_item(items))

    for section in ("lean_messages", "tool_messages", "messages"):
        sec = d.get(section)
        if isinstance(sec, dict):
            items = sec.get("errors", [])
            if isinstance(items, list):
                errors.extend(stringify_error_item(x) for x in items)
            elif items:
                errors.append(stringify_error_item(items))

    err = d.get("error")
    if err:
        errors.append(stringify_error_item(err))

    deduped: list[str] = []
    seen: set[str] = set()
    for e in errors:
        msg = " ".join(str(e).split())
        if msg and msg not in seen:
            seen.add(msg)
            deduped.append(msg)
    return deduped


def extract_lean_code(text: str) -> str:
    code_block = re.findall(r"```(?:lean)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if code_block:
        return code_block[0].strip() + "\n"
    return text.strip() + "\n"


def compact_statement_for_prompt(statement: str, max_chars: int) -> str:
    s = statement.strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 3] + "..."


def inject_single_tactic(formal_statement: str, tactic: str) -> str:
    tactic_block = tactic.strip()
    if not tactic_block:
        return formal_statement
    if "sorry" in formal_statement:
        return formal_statement.replace("sorry", tactic_block, 1)
    return formal_statement.rstrip() + "\nby\n  " + tactic_block + "\n"


def summarize_first_change(old_text: str, new_text: str) -> str:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    max_len = max(len(old_lines), len(new_lines))
    for i in range(max_len):
        old_line = old_lines[i] if i < len(old_lines) else "<EOF>"
        new_line = new_lines[i] if i < len(new_lines) else "<EOF>"
        if old_line != new_line:
            return f"line {i + 1}: `{old_line}` -> `{new_line}`"
    return "no textual difference"


def build_user_prompt(
    problem: Problem,
    attempt: int,
    max_attempts: int,
    last_feedback: str,
    solved_memory: list[dict[str, str]],
    max_statement_chars: int,
) -> str:
    memory_text = ""
    if solved_memory:
        lines = []
        for idx, item in enumerate(solved_memory, start=1):
            lines.append(f"[Solved Example {idx}]")
            lines.append("Formal statement:")
            lines.append(
                compact_statement_for_prompt(item["formal_statement"], max_statement_chars)
            )
            lines.append("Proof:")
            lines.append(item["proof"].strip())
            lines.append("")
        memory_text = "\n".join(lines).strip()

    feedback_text = last_feedback.strip() or "No previous error."
    body = f"""
Attempt {attempt}/{max_attempts}

Current formal statement (replace sorry with a valid proof):
{problem.formal_statement.strip()}

Previous AXLE feedback:
{feedback_text}
"""
    body = textwrap.dedent(body).strip()

    if problem.hint:
        body += "\n\nProblem-specific hint:\n" + problem.hint.strip()

    if memory_text:
        body = body + "\n\nUse style hints from these solved examples if relevant:\n" + memory_text
    return body


async def call_llm(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    message = resp.choices[0].message.content if resp.choices else ""
    return extract_lean_code(message or "")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AXLE + LLM auto-prover loop for Lean theorem problems."
    )
    parser.add_argument("--problems", default="problems.yaml", help="Path to YAML problems file.")
    parser.add_argument(
        "--llm-provider",
        choices=["auto", "openrouter", "openai"],
        default="auto",
        help="LLM provider selection. `auto` uses OpenRouter if OPENROUTER_API_KEY exists.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model id. If omitted, provider-specific defaults are used.",
    )
    parser.add_argument(
        "--environment",
        default="lean-4.28.0",
        help="Default AXLE Lean environment if not set per-problem.",
    )
    parser.add_argument("--max-attempts", type=int, default=6, help="Attempts per problem.")
    parser.add_argument(
        "--memory-size",
        type=int,
        default=2,
        help="How many solved examples to include in later prompts.",
    )
    parser.add_argument(
        "--max-statement-chars",
        type=int,
        default=600,
        help="Max chars per memory statement included in prompt.",
    )
    parser.add_argument("--temperature", type=float, default=0.2, help="LLM temperature.")
    parser.add_argument("--max-tokens", type=int, default=1200, help="LLM max output tokens.")
    parser.add_argument(
        "--use-repair",
        action="store_true",
        help="Enable AXLE repair_proofs before retrying with LLM.",
    )
    parser.add_argument(
        "--repairs",
        default="apply_terminal_tactics,remove_extraneous_tactics",
        help="Comma-separated repair_proofs repairs.",
    )
    parser.add_argument(
        "--terminal-tactics",
        default="aesop,simp,rfl",
        help="Comma-separated terminal tactics for repair_proofs.",
    )
    parser.add_argument(
        "--feedback-lines",
        type=int,
        default=12,
        help="How many error lines to feed back into the next LLM attempt.",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs",
        help="Directory to save run artifacts (json summary).",
    )
    parser.add_argument(
        "--fallback-tactics",
        default="omega,nlinarith",
        help="Comma-separated tactics to auto-try after an LLM failure (set empty to disable).",
    )
    parser.add_argument(
        "--fallback-after-attempt",
        type=int,
        default=1,
        help="Start trying fallback tactics only from this attempt number (1-based).",
    )
    parser.add_argument(
        "--verbose-attempts",
        action="store_true",
        help="Print per-attempt prompt snippet, candidate proof, and AXLE errors.",
    )
    return parser


def choose_llm_provider(provider_arg: str) -> str:
    if provider_arg != "auto":
        return provider_arg
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    return "openai"


def resolve_model(provider: str, model_arg: str | None) -> str:
    if model_arg:
        return model_arg
    if provider == "openrouter":
        return os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini")
    return os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


def build_llm_client(provider: str) -> AsyncOpenAI:
    if provider == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise SystemExit("OPENROUTER_API_KEY is required for --llm-provider openrouter.")

        headers: dict[str, str] = {}
        referer = os.getenv("OPENROUTER_HTTP_REFERER")
        app_name = os.getenv("OPENROUTER_APP_NAME")
        if referer:
            headers["HTTP-Referer"] = referer
        if app_name:
            headers["X-Title"] = app_name

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": "https://openrouter.ai/api/v1",
        }
        if headers:
            kwargs["default_headers"] = headers
        return AsyncOpenAI(**kwargs)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required for --llm-provider openai.")
    kwargs = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)


async def solve_problem(
    axle: AxleClient,
    llm: AsyncOpenAI,
    problem: Problem,
    args: argparse.Namespace,
    solved_memory: list[dict[str, str]],
) -> dict[str, Any]:
    problem_env = problem.environment or args.environment
    feedback = ""
    attempts: list[dict[str, Any]] = []

    for attempt in range(1, args.max_attempts + 1):
        prompt = build_user_prompt(
            problem=problem,
            attempt=attempt,
            max_attempts=args.max_attempts,
            last_feedback=feedback,
            solved_memory=solved_memory[-args.memory_size :],
            max_statement_chars=args.max_statement_chars,
        )
        candidate = await call_llm(
            client=llm,
            model=args.model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )

        verify = await axle.verify_proof(
            formal_statement=problem.formal_statement,
            content=candidate,
            environment=problem_env,
            ignore_imports=True,
        )

        verify_ok = get_okay(verify)
        verify_errors = extract_errors(verify)
        if args.verbose_attempts:
            prompt_preview = prompt if len(prompt) <= 1200 else prompt[:1200] + "\n...[truncated]"
            print(f"    [attempt {attempt}] prompt:", flush=True)
            print(textwrap.indent(prompt_preview.rstrip(), "      "), flush=True)
            print(f"    [attempt {attempt}] candidate:", flush=True)
            print(textwrap.indent(candidate.rstrip(), "      "), flush=True)
            if verify_errors:
                print("    [attempt errors]", flush=True)
                for line in verify_errors[: args.feedback_lines]:
                    print(f"      - {line}", flush=True)
        attempt_record: dict[str, Any] = {
            "attempt": attempt,
            "prompt": prompt,
            "candidate": candidate,
            "verify": to_dict(verify),
            "verify_ok": verify_ok,
        }

        if verify_ok:
            return {
                "problem_id": problem.problem_id,
                "status": "solved",
                "environment": problem_env,
                "attempts_used": attempt,
                "final_proof": get_content(verify) or candidate,
                "attempt_records": attempts + [attempt_record],
            }

        fallback_errors: list[str] = []
        fallback_tactics = (
            parse_csv(args.fallback_tactics)
            if attempt >= args.fallback_after_attempt
            else []
        )
        if args.verbose_attempts and attempt < args.fallback_after_attempt:
            print(
                f"    [fallback tactics skipped until attempt {args.fallback_after_attempt}]",
                flush=True,
            )
        if fallback_tactics:
            fallback_records: list[dict[str, Any]] = []
            for tactic in fallback_tactics:
                fallback_candidate = inject_single_tactic(problem.formal_statement, tactic)
                fallback_verify = await axle.verify_proof(
                    formal_statement=problem.formal_statement,
                    content=fallback_candidate,
                    environment=problem_env,
                    ignore_imports=True,
                )
                fallback_ok = get_okay(fallback_verify)
                fallback_verify_errors = extract_errors(fallback_verify)
                fallback_records.append(
                    {
                        "tactic": tactic,
                        "candidate": fallback_candidate,
                        "verify_ok": fallback_ok,
                        "verify": to_dict(fallback_verify),
                    }
                )

                if args.verbose_attempts:
                    print(f"    [fallback tactic `{tactic}`] ok={fallback_ok}", flush=True)
                    if fallback_verify_errors:
                        for line in fallback_verify_errors[: args.feedback_lines]:
                            print(f"      - {line}", flush=True)

                if fallback_ok:
                    attempt_record["fallback_attempts"] = fallback_records
                    return {
                        "problem_id": problem.problem_id,
                        "status": "solved",
                        "environment": problem_env,
                        "attempts_used": attempt,
                        "solved_via": f"fallback_tactic:{tactic}",
                        "final_proof": get_content(fallback_verify) or fallback_candidate,
                        "attempt_records": attempts + [attempt_record],
                    }
                fallback_errors.extend(fallback_verify_errors)

            attempt_record["fallback_attempts"] = fallback_records

        repaired_ok = False
        repaired_content = ""
        repair_obj: Any = None
        repair_verify_obj: Any = None
        repair_errors: list[str] = []

        if args.use_repair:
            repairs = parse_csv(args.repairs)
            terminal_tactics = parse_csv(args.terminal_tactics)

            repair_kwargs: dict[str, Any] = {
                "content": candidate,
                "environment": problem_env,
                "ignore_imports": True,
            }
            if repairs:
                repair_kwargs["repairs"] = repairs
            if terminal_tactics:
                repair_kwargs["terminal_tactics"] = terminal_tactics

            repair_obj = await axle.repair_proofs(**repair_kwargs)
            repaired_content = get_content(repair_obj) or candidate
            repair_verify_obj = await axle.verify_proof(
                formal_statement=problem.formal_statement,
                content=repaired_content,
                environment=problem_env,
                ignore_imports=True,
            )
            repaired_ok = get_okay(repair_verify_obj)
            repair_errors = extract_errors(repair_verify_obj) or extract_errors(repair_obj)

            attempt_record["repair"] = to_dict(repair_obj)
            attempt_record["repair_verify"] = to_dict(repair_verify_obj)
            attempt_record["repair_ok"] = repaired_ok

            if args.verbose_attempts:
                changed = repaired_content.strip() != candidate.strip()
                repair_stats = to_dict(repair_obj).get("repair_stats", {})
                print(
                    f"    [repair] changed={changed} ok={repaired_ok}",
                    flush=True,
                )
                if repair_stats:
                    print(f"      repair_stats: {repair_stats}", flush=True)
                if changed:
                    print(
                        f"      first_change: {summarize_first_change(candidate, repaired_content)}",
                        flush=True,
                    )
                if repair_errors:
                    for line in repair_errors[: args.feedback_lines]:
                        print(f"      - {line}", flush=True)

            if repaired_ok:
                return {
                    "problem_id": problem.problem_id,
                    "status": "solved",
                    "environment": problem_env,
                    "attempts_used": attempt,
                    "final_proof": get_content(repair_verify_obj) or repaired_content,
                    "attempt_records": attempts + [attempt_record],
                }

        attempts.append(attempt_record)
        merged_errors = verify_errors + fallback_errors + repair_errors
        if merged_errors:
            feedback = "\n".join(merged_errors[: args.feedback_lines])
        else:
            feedback = "Proof failed without explicit error details."

    return {
        "problem_id": problem.problem_id,
        "status": "failed",
        "environment": problem_env,
        "attempts_used": args.max_attempts,
        "final_proof": "",
        "attempt_records": attempts,
        "last_feedback": feedback,
    }


async def main() -> None:
    args = make_parser().parse_args()

    if not os.getenv("AXLE_API_KEY"):
        raise SystemExit("AXLE_API_KEY is required.")

    llm_provider = choose_llm_provider(args.llm_provider)
    args.model = resolve_model(llm_provider, args.model)

    problems = load_problems(Path(args.problems))
    if not problems:
        raise SystemExit("No problems found.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary_path = output_dir / f"run-{run_id}.json"

    llm = build_llm_client(llm_provider)

    solved_memory: list[dict[str, str]] = []
    results: list[dict[str, Any]] = []

    async with AxleClient() as axle:
        for idx, problem in enumerate(problems, start=1):
            print(f"[{idx}/{len(problems)}] solving {problem.problem_id} ...", flush=True)
            result = await solve_problem(axle=axle, llm=llm, problem=problem, args=args, solved_memory=solved_memory)
            results.append(result)

            if result["status"] == "solved":
                solved_memory.append(
                    {
                        "formal_statement": problem.formal_statement,
                        "proof": result["final_proof"],
                    }
                )
                print(
                    f"  -> solved in {result['attempts_used']} attempt(s)",
                    flush=True,
                )
            else:
                print("  -> failed", flush=True)

    solved_count = sum(1 for r in results if r["status"] == "solved")
    payload = {
        "run_id": run_id,
        "llm_provider": llm_provider,
        "model": args.model,
        "environment_default": args.environment,
        "solved": solved_count,
        "total": len(results),
        "results": results,
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Done. Solved {solved_count}/{len(results)}. Summary: {summary_path}")


if __name__ == "__main__":
    asyncio.run(main())
