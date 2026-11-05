"""SQLAlchemy models. See new_schema.md for the design.

Only portable column types are used, so the same models create tables on
SQLite, PostgreSQL and MySQL. Column names are camelCase to match the schema doc.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Text is only 64 KB on MySQL, so ask for LONGTEXT there.
LongText = Text().with_variant(mysql.LONGTEXT(), "mysql")


class Base(DeclarativeBase):
    pass


class Brand(Base):
    """Partner equivalent. Read-only for the API."""

    __tablename__ = "brand"

    brand_id: Mapped[str] = mapped_column("brandId", String(32), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3))


class Dish(Base):
    __tablename__ = "dish"
    __table_args__ = (UniqueConstraint("brandId", "name", name="uq_dish_brand_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    brand_id: Mapped[str] = mapped_column(
        "brandId", String(32), ForeignKey("brand.brandId"), index=True
    )
    name: Mapped[str] = mapped_column(String(50))
    price: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0.00"))
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    course: Mapped[str | None] = mapped_column(String(5))
    extra: Mapped[str | None] = mapped_column(LongText)


class DishText(Base):
    __tablename__ = "dishText"

    dish_id: Mapped[int] = mapped_column(
        "dishId", ForeignKey("dish.id"), primary_key=True
    )
    lang_code: Mapped[str] = mapped_column("langCode", String(2), primary_key=True)
    text: Mapped[str | None] = mapped_column(String(2048))


class Menu(Base):
    __tablename__ = "menu"
    __table_args__ = (UniqueConstraint("brandId", "name", name="uq_menu_brand_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    brand_id: Mapped[str] = mapped_column(
        "brandId", String(32), ForeignKey("brand.brandId"), index=True
    )
    name: Mapped[str] = mapped_column(String(250))


class MenuDish(Base):
    """Pure link table: menu to dish."""

    __tablename__ = "menuDish"

    menu_id: Mapped[int] = mapped_column(
        "menuId", ForeignKey("menu.id"), primary_key=True
    )
    dish_id: Mapped[int] = mapped_column(
        "dishId", ForeignKey("dish.id"), primary_key=True
    )


class MenuRule(Base):
    """No natural key on purpose: on every load the live rules are expired and
    the file's rules are inserted as new rows."""

    __tablename__ = "menuRule"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    menu_id: Mapped[int] = mapped_column("menuId", ForeignKey("menu.id"), index=True)
    region: Mapped[str | None] = mapped_column(String(10))
    channel: Mapped[str | None] = mapped_column(String(24))
    priority: Mapped[int] = mapped_column(default=1)
    active_date: Mapped[datetime | None] = mapped_column("activeDate", DateTime)
    expire_date: Mapped[datetime | None] = mapped_column("expireDate", DateTime)
