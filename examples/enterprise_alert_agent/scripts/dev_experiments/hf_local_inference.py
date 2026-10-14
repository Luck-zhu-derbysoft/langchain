import argparse
from collections.abc import Callable
from typing import Any

DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
TextGenerator = Callable[..., list[dict[str, Any]]]


def generate_reply(
    prompt: str,
    *,
    model_name: str = DEFAULT_MODEL,
    generator: TextGenerator | None = None,
) -> str:
    # """Generate a response using a local Hugging Face model."""
    # if not prompt.strip():
    #     raise ValueError("Prompt cannot be empty.")
    # if generator is None:
    #     from transformers import pipeline

    #     generator = pipeline(
    #         task="text-generation",
    #         model=model_name,
    #         device=-1,
    #     )
    # outputs = generator(
    #     prompt,
    #     max_new_tokens=128,
    #     do_sample=False,
    #     return_full_text=False,
    # )  # type: ignore
    # return str(outputs[0]["generated_text"]).strip()
    return "这是本地模型回答"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "prompt",
    )
    parser.add_argument(
        "--model_name",
        default=DEFAULT_MODEL,
    )
    args = parser.parse_args()
    print(generate_reply(args.prompt, model_name=args.model_name))


if __name__ == "__main__":
    main()
