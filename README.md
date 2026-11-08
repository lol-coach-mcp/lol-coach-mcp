# lol-coach-mcp

내 롤 전적을 분석해 코칭해 주는 MCP 서버입니다.
Claude 같은 MCP 클라이언트에 자연어로 물어보면 Riot 공식 API로 경기를 가져와 분석합니다.

## 할 수 있는 것

- 내 계정 등록과 코칭 설정 기억
- 경기 동기화 (내 PC의 SQLite에 저장)
- 프로필·랭크 조회
- 최근 전적 조회
- 한 판 상세 조회

기능은 계속 추가하고 있습니다.

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

프로젝트 폴더에 `.env` 파일을 만들고 키를 넣습니다.

```
RIOT_API_KEY=RGAPI-xxxxxxxx
```

## 클라이언트에 연결

**Claude Code**

```bash
claude mcp add lol-coach -- uv --directory /경로/lol-coach-mcp run lol-coach-mcp
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

## 사용 예

```
내 계정은 이름#KR1 이야. 등록해 줘.
최근 10판 전적 보여줘.
방금 판 상세 보여줘.
```

## 참고

- KR 서버, 한국어만 지원합니다.
- 경기 데이터와 설정은 내 PC에만 저장됩니다.

## 라이선스

MIT

---

lol-coach-mcp is not endorsed by Riot Games and does not reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc.
