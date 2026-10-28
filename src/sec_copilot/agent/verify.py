"""Check that the numbers in an answer really appear in the sources the model saw."""
import re
from dataclasses import dataclass

SCALES = {"trillion": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6,
          "thousand": 1e3, "k": 1e3}

# optional minus, optional $, digits with commas/decimals, optional unit word, optional %
NUMBER = re.compile(
    r"(?<![\w.%])([-−])?(\$)?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s?(trillion|billion|million|thousand|bn|mn|[bmk])(?![a-z]))?(\s?%)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Mention:
    text: str
    value: float
    checkable: bool     # False for things like years, "8 quarters" and day numbers, which we don't verify


def extract_mentions(text: str) -> list[Mention]:
    """Every number in the text, with units applied: '$54.2 billion' -> 54200000000.0, '379.27%' -> 379.27."""
    mentions = []
    for match in NUMBER.finditer(text):
        sign, dollar, digits, unit, percent = match.groups()
        value = float(digits.replace(",", "")) * SCALES.get((unit or "").lower(), 1.0)
        if sign:
            value = -value
        is_year = (not (dollar or unit or percent) and "." not in digits and "," not in digits
                   and len(digits) == 4 and 1900 <= value <= 2100)
        checkable = not is_year and bool(
            "." in digits or dollar or unit or percent or abs(value) >= 1000
        )
        mentions.append(Mention(match.group(0).strip(), value, checkable))
    return mentions


def numbers_match(value: float, candidates, rel_tol: float = 0.005, abs_tol: float = 0.0) -> bool:
    """Is the value within rounding distance of any candidate? 0.5% allows '$109.4 billion' for 109,417,000,000."""
    return any(abs(value - c) <= max(abs_tol, rel_tol * abs(c)) for c in candidates)


def source_numbers(sources: dict[str, dict]) -> list[float]:
    """All numbers the model could legitimately have read: SQL result cells and numbers inside filing excerpts."""
    values: list[float] = []
    for source in sources.values():
        if source["type"] == "sql":
            values += [float(cell) for row in source["rows"] for cell in row
                       if isinstance(cell, (int, float)) and not isinstance(cell, bool)]
        else:
            values += [m.value for m in extract_mentions(source["text"])]
    return values


def unsupported_numbers(answer: str, sources: dict[str, dict]) -> list[str]:
    """Numbers in the answer that appear in none of the sources: the model made them up or computed them itself."""
    known = source_numbers(sources)
    return [m.text for m in extract_mentions(answer) if m.checkable and not numbers_match(m.value, known)]