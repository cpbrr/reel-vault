from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SavedPost:
    """Metadata for one saved Instagram post, independent of instaloader."""

    shortcode: str
    kind: str  # "video" | "image" | "carousel"
    owner: str
    posted_at: datetime
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        path = "reel" if self.kind == "video" else "p"
        return f"https://www.instagram.com/{path}/{self.shortcode}/"
