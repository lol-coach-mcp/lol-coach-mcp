"""stdio MCP 서버 진입점. stdout은 MCP 채널이므로 출력은 stderr로만 함."""
from __future__ import annotations

import sys

from lol_coach_mcp.config import ConfigError, load_settings
from lol_coach_mcp.server import create_server


def main() -> None:
    try:
        load_settings()
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)
    create_server().run()


if __name__ == "__main__":
    main()
