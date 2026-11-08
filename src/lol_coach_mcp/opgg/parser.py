"""OP.GG MCP 응답 파서.

OP.GG MCP 도구는 JSON 또는 압축 텍스트 DSL로 응답한다. DSL 형식:

    class LolListChampions: data
    class Data: champions
    class Champion: champion_id,key,name

    LolListChampions(Data([Champion(1,"Annie","애니"), ...]))

`class` 줄이 필드 순서를 정의하고, 마지막 생성자 표기의 위치 인자를 그 필드에 대응시킨다.
결과는 일반 dict/list로 돌려준다(최상위 클래스도 dict).
"""
from __future__ import annotations

import json
import re
from typing import Any

_CLASS_RE = re.compile(r"^class\s+([A-Za-z_]\w*)\s*:\s*(.*)$")
_NUMBER_RE = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_NAME_RE = re.compile(r"[A-Za-z_]\w*")
_LITERALS = {"true": True, "false": False, "null": None}
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t", "r": "\r",
            "b": "\b", "f": "\f"}


class OpggParseError(ValueError):
    """OP.GG 응답을 해석할 수 없음."""


def parse_opgg(text: str) -> Any:
    """OP.GG 응답 텍스트를 dict/list로 변환. JSON이면 json.loads."""
    s = text.strip()
    if not s:
        raise OpggParseError("빈 응답")
    if s[0] in "{[":
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            raise OpggParseError(f"JSON 해석 실패: {e}") from e
    classes: dict[str, list[str]] = {}
    body: list[str] = []
    for line in s.splitlines():
        m = _CLASS_RE.match(line.strip()) if not body else None
        if m:
            fields = [f.strip() for f in m.group(2).split(",") if f.strip()]
            classes[m.group(1)] = fields
        elif line.strip():
            body.append(line)
    if not body:
        raise OpggParseError("생성자 표기가 없음")
    return _Reader("\n".join(body), classes).parse()


class _Reader:
    def __init__(self, src: str, classes: dict[str, list[str]]) -> None:
        self.src = src
        self.pos = 0
        self.classes = classes

    def parse(self) -> Any:
        value = self._value()
        self._ws()
        if self.pos != len(self.src):
            raise self._err("남는 내용이 있음")
        return value

    def _err(self, msg: str) -> OpggParseError:
        return OpggParseError(f"{msg} (위치 {self.pos})")

    def _ws(self) -> None:
        while self.pos < len(self.src) and self.src[self.pos].isspace():
            self.pos += 1

    def _peek(self) -> str:
        self._ws()
        if self.pos >= len(self.src):
            raise self._err("예상치 못한 끝")
        return self.src[self.pos]

    def _value(self) -> Any:
        ch = self._peek()
        if ch == '"':
            return self._string()
        if ch == "[":
            self.pos += 1
            return self._items("]")
        if ch == "-" or ch == "." or ch.isdigit():
            return self._number()
        m = _NAME_RE.match(self.src, self.pos)
        if not m:
            raise self._err(f"알 수 없는 문자 {ch!r}")
        name = m.group(0)
        self.pos = m.end()
        if name in _LITERALS:
            return _LITERALS[name]
        if self._peek() != "(":
            raise self._err(f"식별자 {name} 뒤에 '('가 없음")
        self.pos += 1
        fields = self.classes.get(name)
        if fields is None:
            raise self._err(f"정의되지 않은 클래스 {name}")
        args = self._items(")")
        if len(args) != len(fields):
            raise self._err(f"{name} 인자 수 {len(args)} ≠ 필드 수 {len(fields)}")
        return dict(zip(fields, args))

    def _items(self, close: str) -> list[Any]:
        out: list[Any] = []
        if self._peek() == close:
            self.pos += 1
            return out
        while True:
            out.append(self._value())
            ch = self._peek()
            self.pos += 1
            if ch == close:
                return out
            if ch != ",":
                raise self._err(f"',' 또는 '{close}'가 필요함")

    def _number(self) -> int | float:
        m = _NUMBER_RE.match(self.src, self.pos)
        if not m:
            raise self._err("숫자 형식 오류")
        self.pos = m.end()
        tok = m.group(0)
        if any(c in tok for c in ".eE"):
            return float(tok)
        return int(tok)

    def _string(self) -> str:
        self.pos += 1  # 여는 따옴표
        buf: list[str] = []
        src = self.src
        while self.pos < len(src):
            ch = src[self.pos]
            if ch == '"':
                self.pos += 1
                return "".join(buf)
            if ch == "\\":
                if self.pos + 1 >= len(src):
                    break
                nxt = src[self.pos + 1]
                if nxt == "u" and self.pos + 6 <= len(src):
                    try:
                        buf.append(chr(int(src[self.pos + 2:self.pos + 6], 16)))
                    except ValueError as e:
                        raise self._err("잘못된 유니코드 이스케이프") from e
                    self.pos += 6
                    continue
                buf.append(_ESCAPES.get(nxt, nxt))
                self.pos += 2
                continue
            buf.append(ch)
            self.pos += 1
        raise self._err("닫히지 않은 문자열")
