"""환경 변수 및 .env 기반 설정 로딩."""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from . import __version__


class ConfigError(Exception):
    """설정이 올바르지 않을 때 발생."""


@dataclass(frozen=True)
class Settings:
    riot_api_key: str
    data_dir: Path
    user_agent: str
    platform_host: str = "https://kr.api.riotgames.com"
    regional_host: str = "https://asia.api.riotgames.com"


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "lol-coach-mcp"
    return Path.home() / ".local" / "share" / "lol-coach-mcp"


def load_settings(
    env: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
) -> Settings:
    """env 인자(None이면 os.environ) > dotenv 파일 순으로 설정을 읽는다."""
    env_map: Mapping[str, str] = os.environ if env is None else env
    path = dotenv_path if dotenv_path is not None else Path.cwd() / ".env"
    file_map: Mapping[str, str | None] = dotenv_values(path) if path.is_file() else {}

    def get(name: str) -> str | None:
        value = env_map.get(name) or file_map.get(name)
        return value.strip() if value and value.strip() else None

    key = get("RIOT_API_KEY")
    if not key:
        raise ConfigError(
            "RIOT_API_KEY가 설정되지 않음. 환경 변수 또는 .env 파일에 "
            "RIOT_API_KEY를 설정하세요."
        )
    data_dir_raw = get("LOL_COACH_DATA_DIR")
    data_dir = Path(data_dir_raw) if data_dir_raw else _default_data_dir()
    return Settings(
        riot_api_key=key,
        data_dir=data_dir,
        user_agent=f"lol-coach-mcp/{__version__}",
    )
