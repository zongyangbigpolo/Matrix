"""Code-only approved catalogue; names and availability are never stored here."""

import json
import re
import unicodedata
from pathlib import Path


def load_catalog(path: str | Path) -> list[tuple[str, ...]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict) or set(data) != {"version", "groups"}
        or type(data["version"]) is not int or data["version"] != 1
    ):
        raise ValueError("Fund catalogue must contain version=1 and groups")
    groups = data["groups"]
    if not isinstance(groups, list) or not groups:
        raise ValueError("Fund catalogue groups must be a nonempty list")
    seen = set()
    result = []
    for group in groups:
        if not isinstance(group, list) or not group:
            raise ValueError("Each fund group must be a nonempty list of share-class codes")
        for code in group:
            if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
                raise ValueError(f"Invalid fund code: {code!r}")
            if code in seen:
                raise ValueError(f"Duplicate fund code: {code}")
            seen.add(code)
        result.append(tuple(group))
    return result


def us_index_category(name: str, fund_type: str) -> str | None:
    """Conservative first-release scope: RMB Nasdaq 100 / S&P 500 index funds."""
    name = unicodedata.normalize("NFKC", name).replace(" ", "")
    if any(word in name for word in ("美元", "美钞", "美汇", "美金", "港币", "港元")):
        return None
    if fund_type not in {"指数型-海外股票", "QDII-FOF"}:
        return None
    if "ETF" in name.upper() and "联接" not in name:
        return None
    if "纳斯达克100" in name or "纳指100" in name:
        return "纳斯达克100"
    if "标普500" in name:
        return "标普500等权" if "等权" in name else "标普500"
    return None


def parse_candidates(text: str, known_codes: set[str]) -> list[dict[str, str]]:
    match = re.fullmatch(r"\s*var\s+r\s*=\s*(\[.*\])\s*;?\s*", text, re.DOTALL)
    if not match:
        raise ValueError("Unexpected public fund-name response")
    rows = json.loads(match.group(1))
    if not rows:
        raise ValueError("Public fund-name response is empty")
    candidates = []
    seen = set()
    for row in rows:
        if (
            not isinstance(row, list) or len(row) != 5
            or not all(isinstance(value, str) for value in row)
            or not re.fullmatch(r"[0-9]{6}", row[0]) or row[0] in seen
        ):
            raise ValueError("Public fund-name schema changed or contains duplicate codes")
        seen.add(row[0])
        category = us_index_category(row[2], row[3])
        if category and row[0] not in known_codes:
            candidates.append({"code": row[0], "name": row[2], "category": category})
    return sorted(candidates, key=lambda item: item["code"])
