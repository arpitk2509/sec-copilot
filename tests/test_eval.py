import json
from pathlib import Path

from sec_copilot.agent.verify import extract_mentions, numbers_match, unsupported_numbers
from sec_copilot.eval import run_eval
from sec_copilot.eval.scoring import score_case
from sec_copilot.tools.sql_safety import validate_and_limit
from tests.test_agent import FakeToolbox, ScriptedGemini, text_reply, tool_reply


def values(text):
    return [m.value for m in extract_mentions(text)]


# ---------- reading numbers ----------

def test_numbers_are_read_with_their_units():
    assert values("$54,229,000,000") == [54229000000.0]
    assert values("about $109.4 billion") == [109.4e9]
    assert values("revenue of 54.2B and 1.5 million") == [54.2e9, 1.5e6]
    assert values("grew 379.27% year-over-year") == [379.27]
    assert values("margin fell to -11.88% and −3.5%") == [-11.88, -3.5]


def test_years_dates_and_small_counts_are_not_checked():
    flags = {m.text: m.checkable for m in extract_mentions("In fiscal 2026 over 8 quarters, up 2 months, on 2026-09-03 [Q1][C12]")}
    assert flags == {"2026": False, "8": False, "2": False, "09": False, "03": False}
    assert not any(m.checkable for m in extract_mentions("2 months"))      # 'm' of 'months' is not a unit
    assert all(m.checkable for m in extract_mentions("$5, 12%, 3.5, 4,200"))


def test_a_hyphenated_range_is_not_read_as_a_negative_number():
    assert values("between 46.22%-50.06%") == [46.22, 50.06]


def test_rounding_tolerance():
    assert numbers_match(109.4e9, [109417000000.0])           # $109.4 billion
    assert numbers_match(50.0, [50.06])                      # "50%"
    assert not numbers_match(55.0, [50.06])
    assert numbers_match(68.2, [68.234], abs_tol=0.05, rel_tol=0)


# ---------- checking an answer against its sources ----------

SOURCES = {
    "Q1": {"type": "sql", "rows": [["MU", 80.68, 54229000000, 379.27, None]]},
    "C1": {"type": "filing", "text": "Revenue grew 12% to $3.4 billion in fiscal 2025."},
}


def test_numbers_that_are_in_the_sources_pass():
    answer = "Micron's margin was 80.68% on $54.2 billion of revenue, up 379%. A segment grew 12% to $3.4 billion [C1]."
    assert unsupported_numbers(answer, SOURCES) == []


def test_numbers_that_are_not_in_the_sources_are_flagged():
    answer = "Margin was 80.68%, but I estimate growth of 12.5% and revenue of $60 billion."
    assert unsupported_numbers(answer, SOURCES) == ["12.5%", "$60 billion"]


# ---------- scoring a case ----------

CASE = {"must_call": ["run_sql"], "numbers": "all", "expected": None, "must_say_any": ["micron"], "max_unsupported": 0}


def score(case=None, answer="Micron's margin was 80.68%.", tools=("run_sql",), cited=None, expected=(80.68,),
          unsupported=(), invented=()):
    return score_case(case or CASE, answer, list(tools), cited or {}, list(expected), list(unsupported), list(invented))


def test_a_good_answer_passes():
    assert score() == []


def test_each_kind_of_failure_is_reported():
    assert "did not call run_sql" in score(tools=())[0]
    assert "missing expected numbers" in score(answer="Micron did well.")[0]
    assert "says none of" in score(answer="It was 80.68%.")[0]
    assert "not found in the sources" in score(unsupported=["12.5%"])[0]
    assert "invented citation" in score(invented=["C9"])[0]
    assert "golden SQL returned no numbers" in score(expected=())[0]


def test_numbers_any_needs_just_one_and_tolerances_come_from_the_case():
    case = {"numbers": "any", "abs_tol": 0.05}
    assert score(case, "It was -11.9%.", expected=(-11.88, -5.0)) == []
    assert score(case, "It was 7%.", expected=(-11.88, -5.0)) != []


def test_refusals_must_not_use_tools_and_citations_must_include_the_company():
    refuse = {"must_not_call": "all"}
    assert score(refuse, tools=("run_sql",)) != [] and score(refuse, tools=()) == []
    doc = {"cited_ticker": "NVDA"}
    assert score(doc, cited={"C1": {"type": "filing", "ticker": "AMD"}}) != []
    assert score(doc, cited={"C1": {"type": "filing", "ticker": "NVDA"}}) == []
    assert score({"must_not_say_any": ["no quarterly data"]}, "There is no quarterly data.") != []


# ---------- the golden set and the runner ----------

def test_the_golden_set_is_well_formed_and_its_sql_is_allowed():
    cases = json.loads(Path("eval/golden_set.json").read_text())
    assert len(cases) >= 12 and len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        assert {"id", "type", "question"} <= set(case)
        if "expected_sql" in case:
            validate_and_limit(case["expected_sql"])
            assert case.get("numbers") in ("all", "any")
        if case.get("numbers"):
            assert "expected_sql" in case


def test_the_runner_scores_cases_and_summarizes(monkeypatch):
    monkeypatch.setattr(run_eval, "run_query", lambda engine, sql: {"rows": [[80.68]]})
    cases = [
        {"id": "good", "type": "sql", "question": "q1", "expected_sql": "x", "numbers": "all", "must_call": ["run_sql"]},
        {"id": "bad", "type": "sql", "question": "q2", "expected_sql": "x", "numbers": "all", "must_call": ["run_sql"]},
    ]
    gemini = ScriptedGemini([tool_reply(("run_sql", {"sql": "SELECT 1"})), text_reply("It is 80.68% [Q1]."),
                             text_reply("I do not know.")])
    toolbox = FakeToolbox({"run_sql": {"citation": "Q1", "rows": [[80.68]]}})
    toolbox.sources = {"Q1": {"type": "sql", "rows": [[80.68]]}}
    logs = []
    results = run_eval.run_golden_set(cases, gemini, lambda: toolbox, readonly_engine=None, log=logs.append)
    assert [r["passed"] for r in results] == [True, False]
    assert any("FAIL  bad" in line and "did not call run_sql" in line for line in logs)
    summary = []
    run_eval.summarize(results, log=summary.append)
    assert "Passed 1 of 2" in summary[0] and "sql" in summary[1]


def test_the_runner_stops_cleanly_when_the_model_becomes_unavailable(monkeypatch):
    from sec_copilot.llm.client import LLMUnavailable

    class Down:
        def generate(self, *args, **kwargs):
            raise LLMUnavailable("quota")

    cases = [{"id": "a", "type": "sql", "question": "q"}]
    logs = []
    assert run_eval.run_golden_set(cases, Down(), lambda: FakeToolbox(), None, log=logs.append) == []
    assert "STOPPED" in logs[0]




def test_the_prompt_contains_the_lessons_from_the_stress_test():
    from sec_copilot.agent.prompts import SYSTEM_PROMPT
    assert "ROW_NUMBER() OVER (PARTITION BY ticker" in SYSTEM_PROMPT
    assert "financial numbers ARE in the database" in SYSTEM_PROMPT
    assert "Before saying a number from the DATABASE section is unavailable" in SYSTEM_PROMPT
    assert "Decline predictions and out-of-scope requests directly, without using tools" in SYSTEM_PROMPT
    assert "never cite a tag you were not given" in SYSTEM_PROMPT



def test_a_comma_after_a_year_is_not_part_of_the_number():
    mentions = extract_mentions("In fiscal 2026, Micron grew 379%. Between 2024, 2025 and $1,234,567.5")
    assert [(m.value, m.checkable) for m in mentions] == [
        (2026.0, False), (379.0, True), (2024.0, False), (2025.0, False), (1234567.5, True)]
    assert unsupported_numbers("In fiscal 2026, margin was 80.68%.", SOURCES) == []


def test_golden_sql_results_in_decimal_are_read(monkeypatch):
    from decimal import Decimal
    monkeypatch.setattr(run_eval, "run_query", lambda engine, sql: {"rows": [[Decimal("80.68"), "MU", Decimal("54229000000")]]})
    assert run_eval.expected_numbers(None, {"expected_sql": "x"}) == [80.68, 54229000000.0]


def test_saved_results_include_the_sources_for_debugging(monkeypatch):
    monkeypatch.setattr(run_eval, "run_query", lambda engine, sql: {"rows": [[1]]})
    toolbox = FakeToolbox()
    toolbox.sources = {"Q1": {"type": "sql", "rows": [[1]]}}
    results = run_eval.run_golden_set([{"id": "a", "type": "sql", "question": "q"}],
                                      ScriptedGemini([text_reply("Hi.")]), lambda: toolbox, None, log=lambda m: None)
    assert results[0]["sources"] == {"Q1": {"type": "sql", "rows": [[1]]}}
    json.dumps(results[0])                                                  # must be savable