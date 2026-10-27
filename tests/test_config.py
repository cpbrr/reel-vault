import os

import pytest

from reel_vault.config import load_config, load_env_file


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("REEL_VAULT_PROFILE", "REEL_VAULT_HOME", "RV_TEST_A", "RV_TEST_B"):
        monkeypatch.delenv(var, raising=False)


def test_packaged_profiles_load(monkeypatch, tmp_path):
    monkeypatch.setenv("REEL_VAULT_HOME", str(tmp_path))
    for name in ("local", "homelab"):
        cfg = load_config(name)
        assert cfg.profile == name
        assert cfg.paths.home == tmp_path.resolve()
        assert cfg.paths.state_db == tmp_path.resolve() / "data" / "state.db"
        assert cfg.transcriber == "groq"


def test_homelab_vault_path_expands_home():
    cfg = load_config("homelab")
    assert cfg.paths.vault_dir.as_posix().endswith("reel-vault/vault/2 - Personal/Saved Reels")
    assert "ob" in cfg.required_tools


def test_profile_from_toml_path(tmp_path):
    profile = tmp_path / "custom.toml"
    profile.write_text(
        f'[paths]\nhome = "{tmp_path.as_posix()}"\nvault_dir = "{{home}}/v"\n'
        "[fetch]\nstop_after_known = 5\n",
        encoding="utf-8",
    )
    cfg = load_config(str(profile))
    assert cfg.fetch.stop_after_known == 5
    assert cfg.paths.vault_dir == tmp_path / "v"


def test_unknown_profile_exits():
    with pytest.raises(SystemExit):
        load_config("nope")


def test_env_file_does_not_override_existing(monkeypatch, tmp_path):
    monkeypatch.setenv("RV_TEST_A", "from-env")
    env = tmp_path / ".env"
    env.write_text("# comment\nRV_TEST_A=from-file\nexport RV_TEST_B='quoted'\n\n", encoding="utf-8")
    load_env_file(env)
    assert os.environ["RV_TEST_A"] == "from-env"
    assert os.environ["RV_TEST_B"] == "quoted"
