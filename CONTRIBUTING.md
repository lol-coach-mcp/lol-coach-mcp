# 기여 안내

로컬에서 개발하고 테스트를 돌리는 방법과, 브랜치·커밋·PR 규칙을 정리한 문서입니다.

## 개발 환경

- Python 3.10 이상
- [uv](https://docs.astral.sh/uv/)
- 실제 서버를 띄우거나 실서버 테스트를 돌릴 때만 [Riot API 키](https://developer.riotgames.com/)

```bash
git clone https://github.com/lol-coach-mcp/lol-coach-mcp.git
cd lol-coach-mcp
git checkout dev
uv sync
```

실행용 키는 프로젝트 폴더의 `.env`에 넣습니다. 이 파일은 커밋하지 마세요.

```
RIOT_API_KEY=RGAPI-xxxxxxxx
```

서버는 다음으로 실행합니다.

```bash
uv run lol-coach-mcp
```

## 테스트

기본 테스트는 실제 외부 API를 호출하지 않습니다.

```bash
uv run pytest
```

`pyproject.toml`의 `addopts`가 `-m "not live"`라서, `@pytest.mark.live`가 붙은 테스트 2개는 빠집니다. CI도 같은 명령을 사용하므로 Riot 키가 필요 없습니다.

실서버 테스트를 직접 돌리려면 다음을 실행합니다.

```bash
uv run pytest -m live
```

- Riot 계정 조회 테스트는 `RIOT_API_KEY`가 필요합니다.
- OP.GG MCP 테스트는 키가 없어도 외부 네트워크가 필요합니다.

## 브랜치

- 작업은 `dev`에서 새 브랜치를 따서 진행합니다. 브랜치 이름은 변경 내용이 드러나게 짓습니다. 예: `feat/journal-export`, `fix/sync-gap`
- PR은 `dev`로 보냅니다. `dev`에 직접 push하지 마세요.

## 커밋 메시지

기존 이력과 같이 `타입: 짧은 한국어 설명` 형식을 지킵니다.

쓰이는 타입:

- `feat`: 새 기능
- `fix`: 버그 수정
- `docs`: 문서
- `test`: 테스트
- `refactor`: 동작은 그대로 두고 구조만 정리

예시:

```
feat: 매치업 및 빌드 리포트 도구 구현
fix: 동기화 누락 경기 보충
docs: README 완성 및 배포 설정 정리
test: 타임라인 없음 시 매치 저장 테스트 추가
refactor: 매치 조회 공통 처리 분리
```

## PR 체크리스트

- [ ] `uv run pytest`가 통과한다
- [ ] `.env`나 `RIOT_API_KEY`를 커밋하지 않았다
- [ ] 커밋 메시지가 `타입: 짧은 한국어 설명` 형식을 지킨다
- [ ] 동작이나 설정이 바뀌면 README도 함께 수정했다

## 비밀 정보

다음 값은 절대 커밋하거나 이슈·PR에 붙이지 마세요.

- `.env`, `.env.*` 파일
- `RIOT_API_KEY` 값

`.gitignore`에 `.env`와 `.env.*`가 이미 들어 있습니다.
