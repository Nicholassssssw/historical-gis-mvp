import math
import re


WORDS_PER_ESTIMATED_PAGE = 500
_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_NON_CJK_WORD = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
_EXPLICIT_ERA_YEAR = re.compile(r"(?:公元|西元)(前)?\s*(\d{1,4})")
_CALENDAR_YEAR = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")


def count_words(text: str) -> int:
    """Count each Han character and each non-Han word as one word."""
    cjk_count = len(_CJK_CHARACTER.findall(text))
    without_cjk = _CJK_CHARACTER.sub(" ", text)
    return cjk_count + len(_NON_CJK_WORD.findall(without_cjk))


def document_metrics(text: str, actual_pages: int | None = None) -> dict:
    word_count = count_words(text)
    if actual_pages is not None:
        return {
            "word_count": word_count,
            "page_count": actual_pages,
            "page_count_estimated": False,
            "words_per_estimated_page": WORDS_PER_ESTIMATED_PAGE,
        }
    page_count = max(1, math.ceil(word_count / WORDS_PER_ESTIMATED_PAGE))
    return {
        "word_count": word_count,
        "page_count": page_count,
        "page_count_estimated": True,
        "words_per_estimated_page": WORDS_PER_ESTIMATED_PAGE,
    }


def numeric_year_from_period(period: str | None) -> int | None:
    """Keep a numeric year for historical providers when the period includes one."""
    if not period:
        return None
    explicit_match = _EXPLICIT_ERA_YEAR.search(period)
    if explicit_match:
        year = int(explicit_match.group(2))
        return -year if explicit_match.group(1) else year
    match = _CALENDAR_YEAR.search(period)
    if not match:
        return None
    year = int(match.group(1))
    return year
