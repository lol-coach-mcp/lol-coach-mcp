import pytest

from lol_coach_mcp.config import ConfigError, load_settings


def test_loads_key_and_default_data_dir(tmp_path):
    s = load_settings(env={"RIOT_API_KEY": "RGAPI-test", "LOL_COACH_DATA_DIR": str(tmp_path)}, dotenv_path=tmp_path / "none.env")
    assert s.riot_api_key == "RGAPI-test"
    assert s.data_dir == tmp_path
    assert s.user_agent == "lol-coach-mcp/0.1.0"


def test_missing_key_raises(tmp_path):
    with pytest.raises(ConfigError, match="RIOT_API_KEY"):
        load_settings(env={}, dotenv_path=tmp_path / "none.env")


def test_reads_dotenv_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text("RIOT_API_KEY=RGAPI-file\n", encoding="utf-8")
    assert load_settings(env={}, dotenv_path=p).riot_api_key == "RGAPI-file"


def test_repr_hides_api_key(tmp_path):
    s = load_settings(env={"RIOT_API_KEY": "RGAPI-secret", "LOL_COACH_DATA_DIR": str(tmp_path)}, dotenv_path=tmp_path / "none.env")
    assert "RGAPI-secret" not in repr(s)
    assert s.riot_api_key == "RGAPI-secret"
