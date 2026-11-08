"""패키지에 포함된 배포 기준선(data/baselines.json)을 store에 적재."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from importlib import resources

from lol_coach_mcp.store import SOURCE_DIST, Store

PACKAGED_NAME = "baselines.json"
REQUIRED_ROW_KEYS = ("tier", "position", "metric", "mean")

log = logging.getLogger(__name__)


def read_packaged_baselines() -> str | None:
    """패키지 데이터의 배포 기준선 원문. 파일이 없으면 None, UTF-8이 아니면 UnicodeDecodeError."""
    try:
        res = resources.files("lol_coach_mcp").joinpath("data", PACKAGED_NAME)
        if not res.is_file():
            return None
        return res.read_text(encoding="utf-8")
    except (OSError, ModuleNotFoundError):
        return None


def _parse_time(value: str | None) -> datetime | str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return dt


def _is_older(stored: str | None, incoming: str) -> bool:
    """저장된 수집 시각이 새 파일의 수집 시각보다 오래됐는지. 비교할 수 없으면 문자열 비교."""
    a, b = _parse_time(stored), _parse_time(incoming)
    if a is None:
        return True
    if isinstance(a, datetime) and isinstance(b, datetime):
        if (a.tzinfo is None) != (b.tzinfo is None):
            a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
        return a < b
    return str(stored) < incoming


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_row(r: dict) -> None:
    for k in ("tier", "position", "metric"):
        if not isinstance(r[k], str) or not r[k]:
            raise ValueError(f"{k}가 문자열이 아님")
    if r.get("champion") is not None and not isinstance(r["champion"], str):
        raise ValueError("champion이 문자열이 아님")
    if not _is_num(r["mean"]):
        raise ValueError("mean이 숫자가 아님")
    if r.get("std") is not None and not _is_num(r["std"]):
        raise ValueError("std가 숫자가 아님")
    n = r.get("n")
    if n is not None and (not isinstance(n, int) or isinstance(n, bool) or n < 0):
        raise ValueError("n이 0 이상의 정수가 아님")
    for k in ("patch", "collected_at"):
        if r.get(k) is not None and not isinstance(r[k], str):
            raise ValueError(f"{k}가 문자열이 아님")


def _parse(text: str) -> tuple[str, list[dict]]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("최상위가 객체가 아님")
    meta, rows = data.get("metadata"), data.get("rows")
    if not isinstance(meta, dict) or not isinstance(meta.get("collected_at"), str):
        raise ValueError("metadata.collected_at 없음")
    if not isinstance(rows, list):
        raise ValueError("rows가 목록이 아님")
    out = []
    for r in rows:
        if not isinstance(r, dict) or any(r.get(k) is None for k in REQUIRED_ROW_KEYS):
            raise ValueError("필수 필드가 없는 행이 있음")
        _validate_row(r)
        out.append({**r, "champion": r.get("champion") or None, "source": SOURCE_DIST,
                    "collected_at": r.get("collected_at") or meta["collected_at"]})
    return meta["collected_at"], out


def load_distributed_baselines(store: Store, text: str | None) -> bool:
    """배포 기준선을 적재함. 적재했으면 True.

    store에 배포 행이 없거나 그 수집 시각이 파일보다 오래되면 배포 행 전체를 교체함.
    사용자 수집 행은 건드리지 않음. 파일이 없으면 아무 일도 하지 않고, 손상됐으면 경고만 남기고 건너뜀.
    """
    if text is None:
        return False
    try:
        file_collected_at, rows = _parse(text)
    except (ValueError, TypeError) as e:  # JSONDecodeError는 ValueError의 하위 클래스
        log.warning("배포 기준선 파일이 손상되어 적재를 건너뜀: %s", e)
        return False
    stored = [r["collected_at"] for r in store.list_baselines(SOURCE_DIST) if r["collected_at"]]
    if stored and not _is_older(max(stored), file_collected_at):
        return False
    store.replace_baselines_by_source(SOURCE_DIST, rows)  # 한 트랜잭션: 실패하면 기존 행 유지
    return True


def load_packaged(store: Store) -> bool:
    """서버 시작용: 패키지 파일을 읽어 적재함. 어떤 실패도 경고만 남기고 시작을 막지 않음."""
    try:
        return load_distributed_baselines(store, read_packaged_baselines())
    except (sqlite3.Error, ValueError, TypeError, UnicodeDecodeError, RecursionError) as e:
        log.warning("배포 기준선 적재 중 오류로 건너뜀: %s", e)
        return False
