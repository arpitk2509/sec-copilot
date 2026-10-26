"""Check that your key, your model, and tool calling all work, using 3 model calls."""
from google.genai import types

from sec_copilot.llm.client import Gemini

RUN_SQL = types.Tool(function_declarations=[types.FunctionDeclaration(
    name="run_sql",
    description="Run a read-only SQL query on the financial database and return the rows.",
    parameters_json_schema={
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "One SELECT statement."}},
        "required": ["sql"],
    },
)])


def run(gemini: Gemini, log=print) -> bool:
    log(f"Model: {gemini.model}\n")

    # 1. A plain answer.
    reply = gemini.generate("Reply with exactly the word: OK")
    log(f"1. Plain answer: {reply.text!r}")

    # 2. Does the model ask to use a tool?
    question = "How many companies are in the database? Use the run_sql tool to find out."
    contents = [types.Content(role="user", parts=[types.Part(text=question)])]
    reply = gemini.generate(contents, system_instruction="You answer using tools when asked.", tools=[RUN_SQL])
    calls = reply.function_calls or []
    if not calls:
        log(f"2. The model did NOT ask for the tool. It said: {reply.text!r}")
        return False
    call = calls[0]
    log(f"2. The model asked for the tool: {call.name}({dict(call.args)})")

    # 3. Send a pretend result back and get the final answer.
    contents.append(reply.candidates[0].content)          # the model's own message, unchanged
    contents.append(types.Content(role="user", parts=[
        types.Part.from_function_response(name=call.name, response={"rows": [[15]]})
    ]))
    reply = gemini.generate(contents, system_instruction="You answer using tools when asked.", tools=[RUN_SQL])
    log(f"3. Final answer after the tool result: {reply.text!r}")
    log(f"\nAll good. {gemini.calls_made} model calls used.")
    return True


def main() -> None:
    from google.genai import errors

    try:
        run(Gemini())
    except errors.APIError as error:
        hints = {404: "That model name doesn't exist for your key. Run check_models and pick one from the list.",
                 400: "Bad request or invalid API key.", 403: "The key was rejected or lacks permission.",
                 429: "Quota used up. The daily limit resets at midnight Pacific time."}
        raise SystemExit(f"API error {error.code}. {hints.get(error.code, '')}") from error


if __name__ == "__main__":
    main()