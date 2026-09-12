import math


def estimate_tokens(text: str) -> int:
    """粗略估算中英文混合文本的 token 数。"""
    if not text:
        return 0
    # 中文通常接近 1 字符 1 token，英文约 4 字符 1 token。
    # 使用 2 字符约 1 token，作为保守估算。
    return math.ceil(len(text) / 2)


def trim_text(
    text: str,
    max_tokens: int,
    *,
    keep: str = "both",
) -> str:
    """将文本裁剪到指定 token 预算内。"""
    if not text or max_tokens <= 0:
        return ""

    max_chars = max_tokens * 2
    if len(text) <= max_chars:
        return text

    marker = "\n[...上下文已裁剪...]\n"
    if max_chars <= len(marker):
        return text[:max_chars]

    if keep == "head":
        return text[: max_chars - len(marker)] + marker

    if keep == "tail":
        return marker + text[-(max_chars - len(marker)) :]

    head_size = (max_chars - len(marker)) // 2
    tail_size = max_chars - len(marker) - head_size

    return text[:head_size] + marker + text[-tail_size:]
