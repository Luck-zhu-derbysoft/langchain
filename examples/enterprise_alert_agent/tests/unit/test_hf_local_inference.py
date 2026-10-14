import pytest

from scripts.dev_experiments.hf_local_inference import generate_reply


def test_generate_reply() -> None:
    def fake_generator(prompt: str, **kwargs: object) -> list[dict[str, str]]:
        assert prompt == "测试告警"
        assert kwargs["do_sample"] is False
        return [{"generated_text": "这是本地模型回答"}]

    assert generate_reply("测试告警", generator=fake_generator) == "这是本地模型回答"


def test_generate_reply_rejects_empty_prompt() -> None:
    with pytest.raises(ValueError, match="Prompt cannot be empty."):
        generate_reply(" ")
