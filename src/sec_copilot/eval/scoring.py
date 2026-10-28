"""Score one agent answer against a golden-set case, using simple automatic checks."""
from sec_copilot.agent.verify import extract_mentions, numbers_match


def score_case(case: dict, answer: str, tool_names: list[str], cited_sources: dict[str, dict],
               expected_numbers: list[float], unsupported: list[str], invented_tags: list[str]) -> list[str]:
    """Returns the reasons the answer failed. An empty list means it passed."""
    failures = []
    lowered = answer.lower()

    for tool in case.get("must_call", []):
        if tool not in tool_names:
            failures.append(f"did not call {tool}")
    must_not = case.get("must_not_call", [])
    if must_not == "all" and tool_names:
        failures.append(f"should not have used tools, but called {sorted(set(tool_names))}")
    elif isinstance(must_not, list):
        failures += [f"should not have called {tool}" for tool in must_not if tool in tool_names]

    if case.get("numbers"):
        mode = case["numbers"]
        answer_values = [m.value for m in extract_mentions(answer)]
        found = [numbers_match(e, answer_values, case.get("rel_tol", 0.005), case.get("abs_tol", 0.0))
                 for e in expected_numbers]
        if not expected_numbers:
            failures.append("the golden SQL returned no numbers (the case itself needs fixing)")
        elif mode == "all" and not all(found):
            missing = [e for e, ok in zip(expected_numbers, found) if not ok]
            failures.append(f"answer is missing expected numbers {missing}")
        elif mode == "any" and not any(found):
            failures.append(f"answer contains none of the expected numbers {expected_numbers[:5]}")

    if case.get("must_say_any") and not any(phrase.lower() in lowered for phrase in case["must_say_any"]):
        failures.append(f"answer says none of {case['must_say_any']}")
    for phrase in case.get("must_not_say_any", []):
        if phrase.lower() in lowered:
            failures.append(f"answer says '{phrase}'")

    if case.get("cited_ticker"):
        if not any(s["type"] == "filing" and s["ticker"] == case["cited_ticker"] for s in cited_sources.values()):
            failures.append(f"cites no filing text from {case['cited_ticker']}")

    if len(unsupported) > case.get("max_unsupported", 99):
        failures.append(f"numbers not found in the sources: {unsupported}")
    if invented_tags:
        failures.append(f"invented citation tags {invented_tags}")
    return failures