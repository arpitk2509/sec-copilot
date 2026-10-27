"""Ask the agent a question from the command line."""
import argparse

from sqlalchemy import create_engine

from sec_copilot.agent.agent import answer_question, check_citations
from sec_copilot.agent.toolbox import Toolbox
from sec_copilot.agent.verify import unsupported_numbers
from sec_copilot.config import DATABASE_URL, READONLY_DATABASE_URL
from sec_copilot.llm.client import Gemini, LLMUnavailable
from sec_copilot.rag.embeddings import GeminiEmbedder


def show_sources(sources: dict[str, dict]) -> None:
    for tag, source in sources.items():
        if source["type"] == "sql":
            print(f"\n[{tag}] SQL ({source['total_rows']} rows): {source['sql']}")
            print("     " + " | ".join(source["columns"]))
            for row in source["rows"][:5]:
                print("     " + " | ".join(str(v) for v in row))
        else:
            preview = source["text"][:220].replace("\n", " ")
            print(f"\n[{tag}] {source['ticker']} FY{source['fiscal_year']} {source['section']} chunk {source['chunk_index']}")
            print(f"     {preview}...")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the financial research agent a question.")
    parser.add_argument("question")
    args = parser.parse_args()

    gemini = Gemini()
    toolbox = Toolbox(create_engine(DATABASE_URL), create_engine(READONLY_DATABASE_URL), GeminiEmbedder())
    print(f"Model: {gemini.model}")
    try:
        result = answer_question(args.question, gemini, toolbox, log=lambda message: print(f"  tool -> {message}"))
    except LLMUnavailable as error:
        raise SystemExit(f"The model is unavailable: {error}. Try again in a few minutes. (Daily limits reset at midnight Pacific time.)") from error
    cited, invented = check_citations(result.answer, result.sources)
    print(f"\n{result.answer}")
    print(f"\n--- {result.model_calls} model calls, {len(result.tool_calls)} tool calls"
          + (" (hit the call limit)" if result.hit_call_limit else "") + " ---")
    if invented:
        print(f"WARNING: the answer cites tags that do not exist: {invented}")
    if result.tool_calls and not cited:
        print("WARNING: tools were used but the answer cites no sources.")
    unsupported = unsupported_numbers(result.answer, result.sources)
    if unsupported:
        print(f"WARNING: these numbers do not appear in any source the model saw: {unsupported}")
    show_sources(cited)
    unused = len(result.sources) - len(cited)
    if unused:
        print(f"\n({unused} retrieved results were not cited in the answer.)")


if __name__ == "__main__":
    main()