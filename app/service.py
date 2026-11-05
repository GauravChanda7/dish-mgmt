"""Reads a brand's menu config out of the database and writes one back in.

get_menu_config mirrors RboPartner.get_offer_configuration.
apply_menu_config mirrors create_or_update_offer_configuration. It never
commits: the caller commits (put) or rolls back (validate).
"""

import json
from datetime import datetime, timezone

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from . import models as m
from .schemas import SCHEMA_VERSION, MenuConfig


class BrandNotFound(Exception):
    pass


def _now() -> datetime:
    """Naive UTC, which is how timestamps are stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_db_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _from_db_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat()


def _require_brand(session: Session, brand_id: str) -> None:
    if session.get(m.Brand, brand_id) is None:
        raise BrandNotFound(f"Brand {brand_id!r} not found in the database")


def _json_or_empty(text: str | None) -> dict:
    """NULL or '' becomes {}. Bad JSON raises."""
    return json.loads(text) if text else {}


# --------------------------------------------------------------------------
# DB -> JSON
# --------------------------------------------------------------------------


def get_menu_config(session: Session, brand_id: str) -> dict:
    _require_brand(session, brand_id)

    dishes = session.scalars(select(m.Dish).where(m.Dish.brand_id == brand_id)).all()
    texts: dict[int, dict[str, str]] = {}
    if dishes:
        rows = session.scalars(
            select(m.DishText).where(m.DishText.dish_id.in_([d.id for d in dishes]))
        )
        for row in rows:
            texts.setdefault(row.dish_id, {})[row.lang_code] = row.text

    dish_payload = []
    for d in dishes:
        try:
            extra = _json_or_empty(d.extra)
        except json.JSONDecodeError as exc:
            raise ValueError(f"dish {d.id} has invalid JSON in extra: {exc}") from exc
        dish_payload.append(
            {
                "uid": d.name,
                "course": d.course,
                "extra": extra,
                "featured": bool(d.featured),
                "price": f"{d.price:.2f}",
                "texts": texts.get(d.id, {}),
            }
        )

    # The outer join keeps empty menus. Only this brand's dishes are listed,
    # because a menu may contain another brand's dish.
    menu_rows = session.execute(
        select(m.Menu.id, m.Menu.name, m.Dish.name)
        .outerjoin(m.MenuDish, m.MenuDish.menu_id == m.Menu.id)
        .outerjoin(
            m.Dish, (m.Dish.id == m.MenuDish.dish_id) & (m.Dish.brand_id == brand_id)
        )
        .where(m.Menu.brand_id == brand_id)
    ).all()
    menus: dict[int, dict] = {}
    for menu_id, menu_name, dish_name in menu_rows:
        menu = menus.setdefault(menu_id, {"uid": menu_name, "dishes": []})
        if dish_name:
            menu["dishes"].append(dish_name)

    # Live and future rules only. Expired rules stay in the table.
    rule_rows = session.execute(
        select(m.MenuRule, m.Menu.name)
        .join(m.Menu, m.Menu.id == m.MenuRule.menu_id)
        .where(
            m.Menu.brand_id == brand_id,
            or_(m.MenuRule.expire_date.is_(None), m.MenuRule.expire_date > _now()),
        )
    ).all()
    rules = [
        {
            "active_date": _from_db_time(r.active_date),
            "channel": r.channel,
            "expire_date": _from_db_time(r.expire_date),
            "menu": menu_name,
            "priority": r.priority,
            "region": r.region,
        }
        for r, menu_name in rule_rows
    ]

    return _sorted_config(
        {
            "schema_version": SCHEMA_VERSION,
            "dishes": dish_payload,
            "menus": list(menus.values()),
            "rules": rules,
        }
    )


def _sorted_config(config: dict) -> dict:
    """The same ordering the real tool uses for files: fixed root keys,
    everything nested sorted alphabetically with uid first, lists sorted."""
    config["dishes"].sort(key=lambda d: d["uid"])
    config["menus"].sort(key=lambda g: g["uid"])
    for menu in config["menus"]:
        menu["dishes"].sort()
    config["rules"].sort(
        key=lambda r: (
            r["priority"],
            r["region"] or "",
            r["channel"] or "",
            r["active_date"] or "",
            r["expire_date"] or "",
        )
    )

    def sort_keys(value):
        if isinstance(value, dict):
            keys = sorted(value, key=lambda k: (k != "uid", k))
            return {k: sort_keys(value[k]) for k in keys}
        if isinstance(value, list):
            return [sort_keys(v) for v in value]
        return value

    return {
        "schema_version": config["schema_version"],
        "dishes": sort_keys(config["dishes"]),
        "menus": sort_keys(config["menus"]),
        "rules": sort_keys(config["rules"]),
    }


# --------------------------------------------------------------------------
# JSON -> DB
# --------------------------------------------------------------------------


def apply_menu_config(session: Session, brand_id: str, config: MenuConfig) -> dict:
    """Run the load inside the caller's transaction and return what changed.

    Order matters because of the foreign keys. Nothing is ever deleted from
    dish, menu or menuRule: rows missing from the file are left alone, and
    old rules are expired instead.
    """
    _require_brand(session, brand_id)
    summary = {
        "dishes": {"inserted": 0, "updated": 0},
        "menus": {"inserted": 0},
        "rules": {"expired": 0, "inserted": 0},
    }

    # 1. dishes: match on brand + name. Insert new, update changed.
    existing = {
        d.name: d
        for d in session.scalars(select(m.Dish).where(m.Dish.brand_id == brand_id))
    }
    dish_ids: dict[str, int] = {}
    for d in config.dishes:
        values = {
            "price": d.price,
            "featured": d.featured,
            "course": d.course,
            "extra": json.dumps(d.extra.model_dump(exclude_none=True), sort_keys=True),
        }
        row = existing.get(d.uid)
        if row is None:
            row = m.Dish(brand_id=brand_id, name=d.uid, **values)
            session.add(row)
            summary["dishes"]["inserted"] += 1
        elif any(getattr(row, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(row, k, v)
            summary["dishes"]["updated"] += 1
        existing[d.uid] = row
    session.flush()
    for d in config.dishes:
        dish_ids[d.uid] = existing[d.uid].id

    # 2. texts: delete every row of the file's dishes, then insert the file's.
    if dish_ids:
        session.execute(
            delete(m.DishText).where(m.DishText.dish_id.in_(dish_ids.values()))
        )
    for d in config.dishes:
        for lang, text in d.texts.items():
            session.add(m.DishText(dish_id=dish_ids[d.uid], lang_code=lang, text=text))

    # 3. menus: match on brand + name.
    menu_rows = {
        g.name: g
        for g in session.scalars(select(m.Menu).where(m.Menu.brand_id == brand_id))
    }
    for menu in config.menus:
        if menu.uid not in menu_rows:
            menu_rows[menu.uid] = m.Menu(brand_id=brand_id, name=menu.uid)
            session.add(menu_rows[menu.uid])
            summary["menus"]["inserted"] += 1
    session.flush()

    # 4. menu members: delete and re-insert.
    if config.menus:
        session.execute(
            delete(m.MenuDish).where(
                m.MenuDish.menu_id.in_([menu_rows[g.uid].id for g in config.menus])
            )
        )
    for menu in config.menus:
        for dish_name in sorted(set(menu.dishes)):
            session.add(
                m.MenuDish(menu_id=menu_rows[menu.uid].id, dish_id=dish_ids[dish_name])
            )

    # 5. rules: expire every live rule of the brand's menus, then insert the file's.
    now = _now()
    result = session.execute(
        update(m.MenuRule)
        .where(
            m.MenuRule.menu_id.in_(select(m.Menu.id).where(m.Menu.brand_id == brand_id)),
            or_(m.MenuRule.expire_date.is_(None), m.MenuRule.expire_date > now),
        )
        .values(expire_date=now)
    )
    summary["rules"]["expired"] = result.rowcount
    for r in config.rules:
        session.add(
            m.MenuRule(
                menu_id=menu_rows[r.menu].id,
                region=r.region,
                channel=r.channel,
                priority=r.priority,
                active_date=_to_db_time(r.active_date),
                expire_date=_to_db_time(r.expire_date),
            )
        )
        summary["rules"]["inserted"] += 1
    session.flush()
    return summary
