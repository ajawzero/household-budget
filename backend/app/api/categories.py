"""GET /api/categories: active categories by group, in display order."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SIGNED_IN, Db
from app.api.models import CategoriesOut

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)


@router.get("/categories", response_model=CategoriesOut)
def get_categories(conn: Db) -> dict:
    groups: dict[str, dict] = {}
    for g in conn.execute("SELECT group_key, name FROM category_groups ORDER BY sort, group_key"):
        groups[g["group_key"]] = {"group_key": g["group_key"], "name": g["name"], "categories": []}
    for c in conn.execute(
        """SELECT category_key, name, emoji, kind, group_key FROM categories
           WHERE active = 1 ORDER BY sort, category_key"""
    ):
        if c["group_key"] in groups:
            groups[c["group_key"]]["categories"].append(
                {
                    "category_key": c["category_key"],
                    "name": c["name"],
                    "emoji": c["emoji"],
                    "kind": c["kind"],
                }
            )
    return {"groups": list(groups.values())}
