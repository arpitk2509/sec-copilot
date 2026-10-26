"""List the Gemini text models your API key can use, so you can pick one for LLM_MODEL."""
from sec_copilot.llm.client import make_client, model_name

SKIP = ("image", "tts", "audio", "live", "embedding", "robotics", "computer", "veo", "imagen", "learnlm", "aqa", "native",
        "transcribe", "banana", "omni")

def main() -> None:
    client = make_client()   # keep this in a variable: a client that is thrown away closes its own connection
    models = []
    for model in client.models.list():
        name = model.name.removeprefix("models/")
        if "generateContent" in (model.supported_actions or []) and "gemini" in name and not any(s in name for s in SKIP):
            models.append((name, model.input_token_limit, model.output_token_limit))

    print(f"Currently selected (LLM_MODEL): {model_name()}\n")
    print(f"{len(models)} Gemini text models are available to your key:")
    for name, input_limit, output_limit in sorted(models):
        print(f"  {name:<40} input {input_limit or '?':>9}  output {output_limit or '?':>7}")
    print("\nNext: open Google AI Studio, find the rate-limit / usage page, and look up each model's daily request limit.")


if __name__ == "__main__":
    main()