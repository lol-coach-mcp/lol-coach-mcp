class RiotError(Exception):
    """Riot API 호출 관련 기본 오류."""


class RiotAuthError(RiotError):
    """401/403: 키 문제."""

    def __init__(self, message: str = "Riot API 키가 만료되었거나 올바르지 않음. 개발자 포털에서 키를 확인해야 함.") -> None:
        super().__init__(message)


class RiotNotFound(RiotError):
    """404: 대상 없음."""


class RiotRateLimited(RiotError):
    """429 재시도 소진."""


class RiotServerError(RiotError):
    """5xx 재시도 소진."""
