"""Run the agent on the golden set of questions and score every answer."""
import argparse
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine

from sec_copilot.agent.agent import answer_question, check_citations
from sec_copilot.agent.toolbox import Toolbox
from sec_copilot.agent.verify import unsupported_numbers
from sec_copilot.config import DATABASE_URL, READONLY_DATABASE_URL
from sec_copilot.eval.scoring import score_case
from sec_copilot.llm.client import Gemini, LLMUnavailable
from sec_copilot.rag.embeddings import GeminiEmbedder
from sec_copilot.tools.sql_runner import run_query

GOLDEN_SET = Path("eval/golden_set.json")
RESULTS_DIR = Path("eval/results")


def load_cases(path: Path = GOLDEN_SET) -> list[dict]:
    return json.loads(path.read_text())


def expected_numbers(readonly_engine, case: dict) -> list[float]:
    """Run the case's golden SQL and return every number in the result."""
    if "expected_sql" not in case:
        return []
    result = run_query(readonly_engine, case["expected_sql"])
    return [float(cell) for row in result["rows"] for cell in row
            if isinstance(cell, (int, float, Decimal)) and not isinstance(cell, bool)]


def database_check_failure(readonly_engine, check: dict) -> str | None:
    value = run_query(readonly_engine, check["sql"])["rows"][0][0]
    return None if value == check["equals"] else f"database check failed: got {value}, expected {check['equals']}"


def run_golden_set(cases, gemini, make_toolbox, readonly_engine, log=print) -> list[dict]:
    results = []
    for case in cases:
        toolbox = make_toolbox()
        expected = expected_numbers(readonly_engine, case)
        try:
            agent_result = answer_question(case["question"], gemini, toolbox)
        except LLMUnavailable as error:
            log(f"STOPPED: the model became unavailable ({error}). Results so far are saved.")
            break

        cited, invented = check_citations(agent_result.answer, agent_result.sources)
        unsupported = unsupported_numbers(agent_result.answer, agent_result.sources)
        tool_names = [name for name, _ in agent_result.tool_calls]
        failures = score_case(case, agent_result.answer, tool_names, cited, expected, unsupported, invented)
        failures += [f"a tool crashed: {o}" for o in agent_result.tool_outcomes if "The tool failed" in o]
        if case.get("db_check"):
            problem = database_check_failure(readonly_engine, case["db_check"])
            if problem:
                failures.append(problem)

        results.append({
            "id": case["id"], "type": case["type"], "question": case["question"], "passed": not failures,
            "failures": failures, "answer": agent_result.answer, "model_calls": agent_result.model_calls,
            "tool_calls": agent_result.tool_calls, "unsupported_numbers": unsupported,
            "sources": agent_result.sources,
            "tool_outcomes": agent_result.tool_outcomes,
            "expected_numbers": expected[:10],
        })
        log(f"{'PASS' if not failures else 'FAIL'}  {case['id']:<22} {agent_result.model_calls} calls"
            + ("" if not failures else "\n      - " + "\n      - ".join(failures)))
    return results


def summarize(results: list[dict], log=print) -> None:
    by_type = defaultdict(lambda: [0, 0])
    for r in results:
        by_type[r["type"]][1] += 1
        by_type[r["type"]][0] += r["passed"]
    passed = sum(r["passed"] for r in results)
    log(f"\nPassed {passed} of {len(results)} ({100 * passed // max(len(results), 1)}%)   "
        f"model calls used: {sum(r['model_calls'] for r in results)}")
    for kind, (ok, total) in sorted(by_type.items()):
        log(f"  {kind:<10} {ok}/{total}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the agent on the golden question set.")
    parser.add_argument("--only", help="comma-separated case ids to run, e.g. intel_numbers,msft_avg_8q")
    args = parser.parse_args()

    cases = load_cases()
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    engine, readonly = create_engine(DATABASE_URL), create_engine(READONLY_DATABASE_URL)
    embedder = GeminiEmbedder()
    gemini = Gemini()
    print(f"Model: {gemini.model}, {len(cases)} cases\n")
    results = run_golden_set(cases, gemini, lambda: Toolbox(engine, readonly, embedder), readonly)
    summarize(results)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"model": gemini.model, "results": results}, indent=2, default=str))
    print(f"\nFull answers saved to {out}")


if __name__ == "__main__":
    main()