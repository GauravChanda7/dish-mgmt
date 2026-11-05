"""The JSON file format and its validation rules.

Parsing a file into MenuConfig runs every check, so the same model serves
the validate and put endpoints. Failures surface as HTTP 422.
"""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DishExtra(_Strict):
    """The whitelisted keys of dish.extra. Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid", strict=True)

    badge: str | None = None
    note: str | None = None
    imageKey: str | None = None
    vegan: bool | None = None


class Dish(_Strict):
    uid: str = Field(min_length=1, max_length=50)
    price: Decimal = Field(ge=0, max_digits=8, decimal_places=2)
    featured: bool
    course: str | None = Field(max_length=5)
    extra: DishExtra = Field(default_factory=DishExtra)
    texts: dict[str, str] = Field(default_factory=dict)

    @field_validator("texts")
    @classmethod
    def _check_texts(cls, texts: dict[str, str]) -> dict[str, str]:
        for lang, text in texts.items():
            if len(lang) != 2:
                raise ValueError(f"language code {lang!r} must be 2 characters")
            if len(text) > 2048:
                raise ValueError(f"text for {lang!r} is longer than 2048 characters")
        return texts

    @field_serializer("price")
    def _price_as_string(self, price: Decimal) -> str:
        return f"{price:.2f}"

    @field_serializer("extra")
    def _extra_without_nulls(self, extra: DishExtra) -> dict:
        return extra.model_dump(exclude_none=True)


class Menu(_Strict):
    uid: str = Field(min_length=1, max_length=250)
    dishes: list[str]


class Rule(_Strict):
    menu: str
    region: str | None = Field(max_length=10)
    channel: str | None = Field(max_length=24)
    priority: int = Field(ge=0, le=255)
    active_date: datetime | None
    expire_date: datetime | None

    @field_validator("active_date", "expire_date")
    @classmethod
    def _whole_seconds_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.microsecond:
            raise ValueError("must not have sub-second precision")
        if value.tzinfo is None:  # a date without a time zone is UTC
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_serializer("active_date", "expire_date")
    def _iso(self, value: datetime | None) -> str | None:
        return None if value is None else value.isoformat()

    @property
    def is_catchall(self) -> bool:
        return self.region is None and self.channel is None


class MenuConfig(_Strict):
    schema_version: Literal["1"]
    dishes: list[Dish]
    menus: list[Menu]
    rules: list[Rule]

    @model_validator(mode="after")
    def _cross_checks(self) -> "MenuConfig":
        errors: list[str] = []

        dish_by_name = {}
        for dish in self.dishes:
            if dish.uid in dish_by_name:
                errors.append(f"duplicate dish name {dish.uid!r}")
            dish_by_name[dish.uid] = dish

        menu_names: set[str] = set()
        for menu in self.menus:
            if menu.uid in menu_names:
                errors.append(f"duplicate menu name {menu.uid!r}")
            menu_names.add(menu.uid)

            featured_by_course: dict[str, list[str]] = defaultdict(list)
            for name in menu.dishes:
                dish = dish_by_name.get(name)
                if dish is None:
                    errors.append(f"menu {menu.uid!r} uses unknown dish {name!r}")
                elif dish.featured and dish.course is not None:
                    featured_by_course[dish.course].append(name)
            for course, names in featured_by_course.items():
                if len(set(names)) > 1:
                    errors.append(
                        f"menu {menu.uid!r} has more than one featured dish "
                        f"for course {course!r}: {sorted(set(names))}"
                    )

        for rule in self.rules:
            if rule.menu not in menu_names:
                errors.append(f"rule uses unknown menu {rule.menu!r}")

        if not any(rule.is_catchall for rule in self.rules):
            errors.append("no catch-all rule (region and channel both null)")

        if errors:
            raise ValueError("; ".join(errors))
        return self

    def warnings(self) -> list[str]:
        """Things that are allowed but probably a mistake. Never an error."""
        seen: dict[tuple, set[str]] = defaultdict(set)
        for rule in self.rules:
            seen[(rule.region, rule.channel, rule.priority)].add(rule.menu)
        return [
            f"rule key (region={region}, channel={channel}, priority={priority}) "
            f"points at several menus: {sorted(menus)}"
            for (region, channel, priority), menus in seen.items()
            if len(menus) > 1
        ]
