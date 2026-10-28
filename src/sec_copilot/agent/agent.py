"""The conversation loop: the model asks for tools, we run them, and it writes a cited answer."""
import re
from dataclasses import dataclass, field

from google.genai import types

from sec_copilot.agent.prompts import FINAL_ANSWER_NOTICE, SYSTEM_PROMPT
from sec_copilot.agent.toolbox import TOOL_DECLARATIONS

MAX_MODEL_CALLS = 4   # keeps one question within about 4 of the 500 free calls per day
CITATION_BRACKET = re.compile(r"\[([QC]\d+(?:\s*[,;]\s*[QC]\d+)*)\]")   # [C1] or [C1, C4]
CITATION_TAG = re.compile(r"[QC]\d+")


@dataclass
class AgentResult:
    answer: str
    sources: dict[str, dict]
    model_calls: int
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    hit_call_limit: bool = False     # True if the last call had to be an answer with no more tools


def check_citations(answer: str, sources: dict[str, dict]) -> tuple[dict[str, dict], list[str]]:
    """Which sources does the answer actually cite, and which tags did it make up?"""
    tags = list(dict.fromkeys(
        tag for group in CITATION_BRACKET.findall(answer) for tag in CITATION_TAG.findall(group)
    ))                                                           # unique, in order of first use
    cited = {tag: sources[tag] for tag in tags if tag in sources}
    invented = [tag for tag in tags if tag not in sources]
    return cited, invented


def render_sources(sources: dict[str, dict]) -> str:
    """The results gathered so far, as plain text the model can read without any tool-call history."""
    lines = []
    for tag, source in sources.items():
        if source["type"] == "sql":
            lines.append(f"[{tag}] SQL result ({source['total_rows']} rows): {source['sql']}")
            lines.append("  columns: " + ", ".join(source["columns"]))
            lines += ["  " + " | ".join(str(value) for value in row) for row in source["rows"]]
        else:
            lines.append(f"[{tag}] {source['ticker']} FY{source['fiscal_year']} {source['section']} excerpt: {source['text']}")
    return "\n".join(lines)


def answer_question(question: str, gemini, toolbox, max_model_calls: int = MAX_MODEL_CALLS, log=None) -> AgentResult:
    contents = [types.Content(role="user", parts=[types.Part(text=question)])]
    tool_log: list[tuple[str, dict]] = []
    fallback = "I could not produce an answer."

    for call_number in range(1, max_model_calls):               # every call except the last may use tools
        reply = gemini.generate(contents, system_instruction=SYSTEM_PROMPT, tools=[TOOL_DECLARATIONS])
        calls = reply.function_calls or []
        if not calls:
            return AgentResult(reply.text or fallback, toolbox.sources, call_number, tool_log)

        contents.append(reply.candidates[0].content)          # the model's own message, unchanged
        responses = []
        for call in calls:
            args = dict(call.args or {})
            tool_log.append((call.name, args))
            if log:
                log(f"{call.name}({args})")
            responses.append(types.Part.from_function_response(name=call.name, response=toolbox.run(call.name, args)))
        contents.append(types.Content(role="user", parts=responses))

    # Last call: a fresh single message with the question and everything gathered, and no tools at all.
    # (Replaying the tool-call history made the model ask for yet another tool, even when tools were forbidden.)
    gathered = render_sources(toolbox.sources) or "(no results)"
    final_prompt = f"Question: {question}\n\nResults gathered so far:\n{gathered}{FINAL_ANSWER_NOTICE}"
    final = gemini.generate([types.Content(role="user", parts=[types.Part(text=final_prompt)])],
                            system_instruction=SYSTEM_PROMPT)
    return AgentResult(final.text or fallback, toolbox.sources, max_model_calls, tool_log,
                       hit_call_limit=bool(tool_log))