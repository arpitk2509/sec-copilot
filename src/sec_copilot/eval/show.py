"""Print the cases of an evaluation run that failed: what the agent did, and what it answered."""
import glob
import json
import re
import sys
from pathlib import Path

BRACKET = re.compile(r"\[[^\]]*\]")    # anything in square brackets, like [C1, C4]


def latest_results_file() -> str:
    files = sorted(glob.glob("eval/results/*.json"))   # names are timestamps, so the last one is the newest
    if not files:
        raise SystemExit("No results yet. Run: uv run python -m sec_copilot.eval.run_eval")
    return files[-1]


def describe(result: dict) -> list[str]:
    status = "PASS" if result["passed"] else "FAIL"
    lines = [f"===== {result['id']} ({result['type']}) {status}, {result['model_calls']} model calls"]
    lines += [f"  failure: {failure}" for failure in result["failures"]]
    outcomes = result.get("tool_outcomes", [])
    for index, (name, args) in enumerate(result["tool_calls"]):
        outcome = f" -> {outcomes[index]}" if index < len(outcomes) else ""
        lines.append(f"  tool: {name} {args}{outcome}")
    sources = result.get("sources", {})
    shown = ", ".join(f"{tag}={source.get('ticker') or 'SQL'}" for tag, source in sources.items())
    lines.append(f"  sources kept: {shown or 'none'}")
    lines.append(f"  tags in the answer: {BRACKET.findall(result['answer'])}")
    lines.append(f"  answer: {result['answer']}")
    return lines


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show_all = "--all" in sys.argv
    path = args[0] if args else latest_results_file()
    data = json.loads(Path(path).read_text())
    print(f"{path}  (model: {data.get('model')})\n")
    chosen = [r for r in data["results"] if show_all or not r["passed"]]
    for result in chosen:
        print("\n".join(describe(result)) + "\n")
    print(f"{len(chosen)} of {len(data['results'])} cases shown" + ("" if show_all else " (failed ones; add --all for every case)"))


if __name__ == "__main__":
    main()