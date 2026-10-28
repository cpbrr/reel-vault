"""Emergent category registry (data/categories.json).

Claude sees the current list for every post and picks one or proposes a new one.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, replace
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

    def unassign(self, name: str) -> None:
        """Undo one assign; a category with no posts left is dropped."""
        category = self.find(name)
        if category is None:
            return
        category.count -= 1
        if category.count <= 0:
            self.categories.remove(category)

    def merge(self, plan: dict[str, dict], counts: dict[str, int]) -> dict[str, str]:
        """Apply a merge plan {new: {"description": ..., "from": [old, ...]}}.

        Categories not mentioned keep their name and description. Counts are reset
        from `counts` (posts per category after the merge). Returns old -> new names.
        """
        renames = {old: new for new, spec in plan.items() for old in spec.get("from", [])}
        merged: dict[str, Category] = {}
        for category in self.categories:
            name = renames.get(category.name, category.name)
            if name not in merged:
                description = plan.get(name, {}).get("description") or category.description
                merged[name] = Category(name=name, description=description)
        for name, spec in plan.items():  # a new name whose sources were all empty
            merged.setdefault(name, Category(name=name, description=spec.get("description", "")))
        for category in merged.values():
            category.count = counts.get(category.name, 0)
        self.categories = [c for c in merged.values() if c.count > 0]
        return renames

    def snapshot(self) -> Registry:
        """Independent copy for a worker thread to read while the original changes."""
        clone = copy.copy(self)
        clone.categories = [replace(c) for c in self.categories]
        return clone

    def prompt_listing(self) -> str:
        if not self.categories:
            return "(none yet; you are creating the first categories)"
        ordered = sorted(self.categories, key=lambda c: -c.count)
        return "\n".join(f"- {c.name}: {c.description} ({c.count} notes)" for c in ordered)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(c) for c in sorted(self.categories, key=lambda c: c.name)]
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
