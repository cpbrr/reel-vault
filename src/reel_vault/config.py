"""Profile + .env loading.

A profile is a TOML file describing one deployment (homelab, mac, local dev).
Packaged profiles live in reel_vault/profiles; a path to any .toml also works.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path

DEFAULT_PROFILE = "local"


@dataclass(frozen=True)
class Paths:
    home: Path
    data_dir: Path
    secrets_dir: Path
    vault_dir: Path

    @property
    def state_db(self) -> Path:
        return self.data_dir / "state.db"

    @property
    def session_file(self) -> Path:
        return self.secrets_dir / "ig-session"

    @property
    def env_file(self) -> Path:
        return self.secrets_dir / ".env"


@dataclass(frozen=True)
class FetchConfig:
    # Once the backlog is done, stop walking saves after this many known posts in a row.
    stop_after_known: int = 20
    # 0 = no cap.
    max_posts_per_run: int = 0
    # Random pause between posts that hit Instagram (media download), in seconds.
    sleep_min: float = 5
    sleep_max: float = 15


@dataclass(frozen=True)
class Config:
    profile: str
    paths: Paths
    fetch: FetchConfig
    transcriber: str
    transcribe_model: str | None
    analyze_model: str
    required_tools: tuple[str, ...]
    # Keep each post's downloads (video, audio, thumbnail, images) after its note
    # is written, under data/media/<shortcode>/. Processed results live in
    # state.db either way.
    keep_media: bool = False

    @property
    def categories_file(self) -> Path:
        return self.paths.data_dir / "categories.json"

    @property
    def media_dir(self) -> Path:
        return self.paths.data_dir / "media"


def load_config(profile: str | None = None) -> Config:
    name = profile or os.environ.get("REEL_VAULT_PROFILE") or DEFAULT_PROFILE
    raw = tomllib.loads(_read_profile(name))

    paths_raw = raw.get("paths", {})
    home = Path(os.environ.get("REEL_VAULT_HOME") or paths_raw.get("home", ".dev"))
    home = home.expanduser().resolve()

    def resolve(key: str, default: str) -> Path:
        value = paths_raw.get(key, default).replace("{home}", str(home))
        return Path(value).expanduser()

    paths = Paths(
        home=home,
        data_dir=resolve("data_dir", "{home}/data"),
        secrets_dir=resolve("secrets_dir", "{home}/secrets"),
        vault_dir=resolve("vault_dir", "{home}/vault/Saved Reels"),
    )
    load_env_file(paths.env_file)
    # Machine-specific vault location (e.g. the real Obsidian vault on this laptop).
    if vault_override := os.environ.get("REEL_VAULT_VAULT_DIR"):
        paths = replace(paths, vault_dir=Path(vault_override).expanduser())

    transcribe = raw.get("transcribe", {})
    return Config(
        profile=name,
        paths=paths,
        fetch=FetchConfig(**raw.get("fetch", {})),
        transcriber=transcribe.get("backend", "groq"),
        transcribe_model=transcribe.get("model"),
        analyze_model=raw.get("analyze", {}).get("model", "sonnet"),
        required_tools=tuple(raw.get("doctor", {}).get("require", [])),
        keep_media=raw.get("storage", {}).get("keep_media", False),
    )


def _read_profile(name: str) -> str:
    candidate = Path(name)
    if candidate.suffix == ".toml":
        return candidate.read_text(encoding="utf-8")
    packaged = resources.files("reel_vault.profiles") / f"{name}.toml"
    if not packaged.is_file():
        raise SystemExit(f"Unknown profile {name!r} (no reel_vault/profiles/{name}.toml)")
    return packaged.read_text(encoding="utf-8")


def load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE loader. Existing environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)
