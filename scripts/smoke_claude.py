"""Phase 0 check: one Claude call per model, each traced in LangSmith.

Run inside the api container:
    docker compose exec api python scripts/smoke_claude.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402
from langsmith import traceable  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.llm import get_claude  # noqa: E402


@traceable(name="phase0_smoke_test")
def main() -> None:
    settings = get_settings()
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set: add it to .env, then `docker compose up -d`.")
    client = get_claude()

    for model in (settings.model_turn, settings.model_reasoning):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=2000,
                messages=[
                    {
                        "role": "user",
                        "content": "You are a mock interviewer. Ask me one short warm-up question.",
                    }
                ],
            )
        except anthropic.AuthenticationError:
            sys.exit("ANTHROPIC_API_KEY is missing or invalid (check .env).")
        except anthropic.NotFoundError:
            sys.exit(f"Model not found: {model}")
        except anthropic.APIStatusError as exc:
            sys.exit(f"{model}: API error {exc.status_code}: {exc.message}")

        if response.stop_reason == "refusal":
            print(f"[{model}] refused: {response.stop_details}")
            continue
        text = "".join(b.text for b in response.content if b.type == "text")
        print(f"[{model}] {text.strip()}")
        print(f"   tokens in/out: {response.usage.input_tokens}/{response.usage.output_tokens}")

    if os.getenv("LANGSMITH_TRACING", "").lower() == "true" and os.getenv("LANGSMITH_API_KEY"):
        print(f"\nTraced to LangSmith project '{settings.langsmith_project}'.")
    else:
        print("\nLangSmith tracing is OFF: set LANGSMITH_TRACING=true and LANGSMITH_API_KEY.")


if __name__ == "__main__":
    main()
