import pytest

from app.rag.splitter.text_splitter import TextSplitter


def test_empty_text_returns_no_chunks() -> None:
    assert TextSplitter(chunk_size=20, chunk_overlap=5).split("  \n\n ") == []


def test_short_text_is_single_chunk() -> None:
    assert TextSplitter(chunk_size=20, chunk_overlap=5).split("hello") == ["hello"]


def test_small_paragraphs_are_merged_up_to_chunk_size() -> None:
    chunks = TextSplitter(chunk_size=20, chunk_overlap=0).split("aaaa\n\nbbbb\n\ncccc")

    assert chunks == ["aaaa\nbbbb\ncccc"]


def test_adjacent_chunks_carry_overlap_from_previous_tail() -> None:
    chunks = TextSplitter(chunk_size=20, chunk_overlap=5).split("A" * 15 + "\n\n" + "B" * 15)

    assert chunks == ["A" * 15, "A" * 5 + "\n" + "B" * 15]


def test_oversized_paragraph_is_force_split() -> None:
    chunks = TextSplitter(chunk_size=20, chunk_overlap=5).split("x" * 50)

    assert len(chunks) > 1
    assert all(len(chunk) <= 20 + 5 + 1 for chunk in chunks)  # 块长 + 重叠 + 换行


def test_zero_overlap_is_respected_instead_of_falling_back_to_settings() -> None:
    splitter = TextSplitter(chunk_size=10, chunk_overlap=0)

    assert splitter._chunk_overlap == 0
    assert splitter.split("y" * 25) == ["y" * 10, "y" * 10, "y" * 5]


@pytest.mark.parametrize("text", ["中文段落一。\n\n中文段落二。", "line1\nline2\nline3"])
def test_no_content_is_lost(text: str) -> None:
    joined = "\n".join(TextSplitter(chunk_size=8, chunk_overlap=0).split(text))

    for token in text.replace("\n\n", "\n").split("\n"):
        assert token in joined
