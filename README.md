# lol-coach-mcp

내 롤 전적을 분석해 코칭해 주는 MCP 서버입니다.
Claude 같은 MCP 클라이언트에 자연어로 물어보면 Riot 공식 API로 경기를 가져와 분석합니다.

## 할 수 있는 것

- `my_profile`: 내 계정 등록과 코칭 설정 기억
- `get_coach_context`: 코칭 시작용 설정·동기화 상태·진행 중 과제 요약
- `sync_matches`: 경기 동기화 (내 PC의 SQLite에 저장)
- `get_profile`: 랭크·숙련도·최근 폼 조회
- `get_match_history`: 최근 전적 조회
- `get_match_detail`: 한 판 상세 조회
- `review_match`: 한 판 복기 (타임라인 근거 포함)
- `analyze_form`: 최근 폼 분석
- `compare_splits`: 기간·챔피언·포지션 등 구간 비교
- `find_habits`: 반복되는 습관 탐지
- `champion_report`: 챔피언별 리포트
- `matchup_report`: 상대 챔피언 매치업 리포트
- `build_report`: 아이템·룬 빌드 리포트
- `goals`: 연습 과제 설정과 진행 판정
- `journal`: 코칭 일지 기록·조회
- `explain`: 지표·기준선 설명
- `open_dashboard`: 로컬 대시보드 열기

복기 프롬프트 4개(`match_review`, `weekly_review`, `season_review`, `session_wrap`)와 리소스 2개(`coach://context`, `coach://baselines`)도 제공합니다.

## 준비물

- Python 3.10 이상
- [uv](https://docs.astral.sh/uv/)
- Riot API 키 ([Riot Developer Portal](https://developer.riotgames.com/)에서 발급)

## 설치

```bash
git clone https://github.com/lol-coach-mcp/lol-coach-mcp.git
cd lol-coach-mcp
uv sync
```

클론 없이 바로 실행할 수도 있습니다.

```bash
uvx --from git+https://github.com/lol-coach-mcp/lol-coach-mcp lol-coach-mcp
```

이 방식은 작업 폴더에 `.env`가 없으므로 아래 클라이언트 설정의 `env`(또는 `-e`)로 `RIOT_API_KEY`를 넘겨야 합니다.

## 키 설정

1. Riot Developer Portal에 로그인합니다.
2. Register Product에서 Personal 제품을 등록합니다.
3. 발급된 키를 프로젝트 폴더의 `.env` 파일에 넣습니다.

```
RIOT_API_KEY=RGAPI-xxxxxxxx
```

## 클라이언트에 연결

**Claude Code**

```bash
# 클론한 폴더 사용 (폴더의 .env에서 키를 읽음)
claude mcp add lol-coach -- uv --directory /경로/lol-coach-mcp run lol-coach-mcp

# 클론 없이 uvx 사용 (키를 직접 전달)
claude mcp add lol-coach -e RIOT_API_KEY=RGAPI-xxxxxxxx -- uvx --from git+https://github.com/lol-coach-mcp/lol-coach-mcp lol-coach-mcp
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "lol-coach": {
      "command": "uv",
      "args": ["--directory", "/경로/lol-coach-mcp", "run", "lol-coach-mcp"]
    }
  }
}
```

`uvx`로 실행하려면 다음처럼 `env`에 키를 넣습니다.

```json
{
  "mcpServers": {
    "lol-coach": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/lol-coach-mcp/lol-coach-mcp", "lol-coach-mcp"],
      "env": { "RIOT_API_KEY": "RGAPI-xxxxxxxx" }
    }
  }
}
```

**Cursor** (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "lol-coach": {
      "command": "uv",
      "args": ["--directory", "/경로/lol-coach-mcp", "run", "lol-coach-mcp"]
    }
  }
}
```

`uvx`로 실행하려면 다음처럼 `env`에 키를 넣습니다.

```json
{
  "mcpServers": {
    "lol-coach": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/lol-coach-mcp/lol-coach-mcp", "lol-coach-mcp"],
      "env": { "RIOT_API_KEY": "RGAPI-xxxxxxxx" }
    }
  }
}
```

데이터는 기본 위치에 저장되며, `LOL_COACH_DATA_DIR` 환경변수로 바꿀 수 있습니다.
`open_dashboard`가 만드는 HTML 리포트는 그 폴더 아래 `reports/`에 저장됩니다.

## 사용 예

```
내 계정은 이름#KR1 이야. 등록해 줘.
최근 10판 전적 보여줘.
방금 판 복기해 줘.
내 반복되는 습관 찾아 줘.
이번 주 연습 과제 정해 줘.
```

## 기준선

티어별 기준선이 배포본에 포함되어 있어 바로 쓸 수 있습니다.
배포본 기준선은 IRON~DIAMOND만 포함하며, MASTER 이상은 기준선이 없어 "내 승리 판 평균"과 비교합니다.
직접 수집하려면 다음을 실행합니다. Riot API 호출 제한 때문에 시간이 오래 걸립니다.

```bash
uv run lol-coach-baseline collect --matches-per-tier 100
uv run lol-coach-baseline build
```

## 참고

- KR 서버, 한국어만 지원합니다.
- 경기 데이터와 설정은 내 PC에만 저장됩니다.
- 챔피언 빌드·매치업 통계는 OP.GG 공개 MCP를 사용합니다.
- 기준선은 같은 티어·포지션 표본의 평균·표준편차 요약이며, 개인 점수나 순위가 아닙니다.

## 라이선스

MIT

---

lol-coach-mcp is not endorsed by Riot Games and does not reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc.
