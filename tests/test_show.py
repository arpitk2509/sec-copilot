from sec_copilot.eval.show import describe

RESULT = {
    "id": "nvidia_export_controls", "type": "doc", "passed": False, "model_calls": 3,
    "failures": ["cites no filing text from NVDA"],
    "tool_calls": [["search_filings", {"query": "export controls", "ticker": "NVDA"}]],
    "tool_outcomes": ["ERROR: The tool failed: boom"],
    "sources": {"C1": {"type": "filing", "ticker": "NVDA"}, "Q1": {"type": "sql"}},
    "answer": "Controls hurt demand [C1, C5] and margins [Q1].",
}


def test_a_failed_case_is_described_with_its_tools_sources_and_tags():
    text = "\n".join(describe(RESULT))
    assert "nvidia_export_controls (doc) FAIL, 3 model calls" in text
    assert "failure: cites no filing text from NVDA" in text
    assert "tool: search_filings {'query': 'export controls', 'ticker': 'NVDA'} -> ERROR: The tool failed: boom" in text
    assert "sources kept: C1=NVDA, Q1=SQL" in text
    assert "tags in the answer: ['[C1, C5]', '[Q1]']" in text


def test_a_case_without_saved_sources_still_prints():
    old = {k: v for k, v in RESULT.items() if k != "sources"}
    assert "sources kept: none" in "\n".join(describe(old))



def test_results_saved_before_outcomes_existed_still_print():
    old = {k: v for k, v in RESULT.items() if k != "tool_outcomes"}
    assert "tool: search_filings {'query': 'export controls', 'ticker': 'NVDA'}\n" in "\n".join(describe(old)) + "\n"