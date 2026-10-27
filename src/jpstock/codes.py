"""銘柄コード入力のパース。"""

from __future__ import annotations

import re
import unicodedata

SEPARATOR = ","
# 4桁 (英字を含む新コード "130A" 等も可) 、または末尾 0 の5桁 (J-Quants 形式 "72030")
_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{2}[0-9A-Z]0?$")


class InvalidCodeError(ValueError):
    pass


def normalize_code(raw: str) -> str:
    """銘柄コードを4桁表記にそろえる。全角入力も受け付ける。"""
    code = unicodedata.normalize("NFKC", raw).strip().upper()
    if not _CODE_RE.match(code):
        raise InvalidCodeError(f"銘柄コードの形式が不正です: {raw!r}")
    return code[:4]


def parse_codes(text: str) -> list[str]:
    """ "7203,6758, 9984" のようなカンマ区切り文字列を銘柄コードのリストにする (重複は除去)。"""
    text = unicodedata.normalize("NFKC", text)  # 全角カンマ「，」も "," になる
    codes: list[str] = []
    errors: list[str] = []
    for part in text.split(SEPARATOR):
        if not part.strip():
            continue
        try:
            code = normalize_code(part)
        except InvalidCodeError as e:
            errors.append(str(e))
            continue
        if code not in codes:
            codes.append(code)
    if errors:
        raise InvalidCodeError("\n".join(errors))
    if not codes:
        raise InvalidCodeError("銘柄コードが指定されていません")
    return codes
