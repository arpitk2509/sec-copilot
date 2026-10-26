"""The conversation loop: the model asks for tools, we run them, and it writes a cited answer."""
import re
from dataclasses import dataclass, field

from google.genai import types

from sec_copilot.agent.prompts import FINAL_ANSWER_NOTICE, SYSTEM_PROMPT
from sec_copilot.agent.toolbox import TOOL_DECLARATIONS

MAX_MODEL_CALLS = 4   # keeps one question within about 4 of the 500 free calls per day
CITATION_TAG = re.compile(r"\[([QC]\d+)\]")


@dataclass
class AgentResult:
    answer: str
    sources: dict[str, dict]
    model_calls: int
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    hit_call_limit: bool = False     # True if the last call had to be an answer with no more tools


def check_citations(answer: str, sources: dict[str, dict]) -> tuple[dict[str, dict], list[str]]:
    """Which sources does the answer actually cite, and which tags did it make up?"""
    tags = list(dict.fromkeys(CITATION_TAG.findall(answer)))     # unique, in order of first use
    cited = {tag: sources[tag] for tag in tags if tag in sources}
    invented = [tag for tag in tags if tag not in sources]
    return cited, invented


def answer_question(question: str, gemini, toolbox, max_model_calls: int = MAX_MODEL_CALLS, log=None) -> AgentResult:
    contents = [types.Content(role="user", parts=[types.Part(text=question)])]
    tool_log: list[tuple[str, dict]] = []

    for call_number in range(1, max_model_calls + 1):
        last_chance = call_number == max_model_calls
        reply = gemini.generate(
            contents,
            system_instruction=SYSTEM_PROMPT + (FINAL_ANSWER_NOTICE if last_chance else ""),
            tools=None if last_chance else [TOOL_DECLARATIONS],
        )
        calls = [] if last_chance else (reply.function_calls or [])

        if not calls:
            answer = reply.text or "I could not produce an answer."
            return AgentResult(answer, toolbox.sources, call_number, tool_log, hit_call_limit=last_chance and bool(tool_log))

        contents.append(reply.candidates[0].content)          # the model's own message, unchanged
        responses = []
        for call in calls:
            args = dict(call.args or {})
            tool_log.append((call.name, args))
            if log:
                log(f"{call.name}({args})")
            responses.append(types.Part.from_function_response(name=call.name, response=toolbox.run(call.name, args)))
        contents.append(types.Content(role="user", parts=responses))

    raise RuntimeError("unreachable: the last call always returns")   # pragma: no cover