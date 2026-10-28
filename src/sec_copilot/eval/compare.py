"""Compare evaluation runs before and after a change. Runs vary a little, so each side can be several runs."""
import json
import sys
from pathlib import Path


def load(path: str) -> dict:
    data = json.loads(Path(path).read_text())
    return {"model": data.get("model", "?"), "results": {r["id"]: r for r in data["results"]}}


def pass_counts(runs: list[dict]) -> dict[str, tuple[int, int]]:
    """For each case: (times passed, times run) across the runs."""
    counts: dict[str, list[int]] = {}
    for run in runs:
        for case_id, result in run["results"].items():
            entry = counts.setdefault(case_id, [0, 0])
            entry[0] += bool(result["passed"])
            entry[1] += 1
    return {case_id: (passed, total) for case_id, (passed, total) in counts.items()}


def compare(old_runs: list[dict], new_runs: list[dict]) -> list[str]:
    old, new = pass_counts(old_runs), pass_counts(new_runs)
    lines = [f"{'case':<28} {'before':<8} {'after':<8}"]
    better = worse = 0
    for case_id, (passed, total) in new.items():
        before = old.get(case_id)
        before_text = "-" if before is None else f"{before[0]}/{before[1]}"
        note = ""
        if before is not None:
            rate_before, rate_after = before[0] / before[1], passed / total
            if rate_after > rate_before:
                note, better = "  <- better", better + 1
            elif rate_after < rate_before:
                note, worse = "  <- WORSE", worse + 1
        lines.append(f"{case_id:<28} {before_text:<8} {f'{passed}/{total}':<8}{note}")

    def average(runs):
        rates = [sum(bool(r["passed"]) for r in run["results"].values()) / max(len(run["results"]), 1) for run in runs]
        return f"{100 * sum(rates) / max(len(rates), 1):.0f}% over {len(runs)} run(s)"

    lines.append(f"\nbefore: {average(old_runs)}   after: {average(new_runs)}   better: {better}   worse: {worse}")
    return lines


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python -m sec_copilot.eval.compare BEFORE1.json,BEFORE2.json AFTER1.json,AFTER2.json")
    old = [load(path) for path in sys.argv[1].split(",")]
    new = [load(path) for path in sys.argv[2].split(",")]
    print("\n".join(compare(old, new)))


if __name__ == "__main__":
    main()