"""Emergent category registry (data/categories.json).

Claude sees the current list for every post and picks one or proposes a new one.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

MEMES = "Memes"


@dataclass
class Category:
    name: str
    description: str
    count: int = 0


class Registry:
    def __init__(self, path: Path):
        self.path = path
        self.categories: list[Category] = []
        if path.is_file():
            self.categories = [Category(**c) for c in json.loads(path.read_text(encoding="utf-8"))]

    def find(self, name: str) -> Category | None:
        key = name.strip().casefold()
        return next((c for c in self.categories if c.name.casefold() == key), None)

    def assign(self, name: str, description: str | None) -> Category:
        """Count a post toward `name`, creating the category if it is new."""
        category = self.find(name)
        if category is None:
            category = Category(name=name.strip(), description=(description or "").strip())
            self.categories.append(category)
        category.count += 1
        return category

    def prompt_listing(self) -> str:
        if not self.categories:
            return "(none yet; you are creating the first categories)"
        ordered = sorted(self.categories, key=lambda c: -c.count)
        return "\n".join(f"- {c.name}: {c.description} ({c.count} notes)" for c in ordered)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(c) for c in sorted(self.categories, key=lambda c: c.name)]
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
