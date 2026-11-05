"""Create the tables and load the sample data. Safe to run again.

    python -m app.seed             # load the sample files every time
    python -m app.seed --if-empty  # only load them into an empty database
"""

from pathlib import Path

import sys

from sqlalchemy import func, select

from . import database
from .models import Base, Brand, Dish
from .schemas import MenuConfig
from .service import apply_menu_config

MENUS_DIR = Path(__file__).resolve().parent.parent / "menus"

# ORCHARD has no file on purpose: GET returns an empty config for it.
BRANDS = {"SUNRISE": "USD", "HARBOR": "CAD", "ORCHARD": "EUR"}


def seed(engine=None, session_factory=None, only_if_empty: bool = False) -> None:
    engine = engine or database.engine
    session_factory = session_factory or database.SessionLocal
    Base.metadata.create_all(engine)

    with session_factory() as session:
        for brand_id, currency in BRANDS.items():
            if session.scalar(select(Brand).where(Brand.brand_id == brand_id)) is None:
                session.add(Brand(brand_id=brand_id, currency=currency))
        session.commit()

        # Every load of a file expires and re-inserts its rules, so a server that
        # seeds on each start would pile up rule rows. --if-empty avoids that.
        if only_if_empty and session.scalar(select(func.count()).select_from(Dish)):
            print("Database already has data, not loading the sample files.")
            return

        # Like upload_script.py: the filename is the brand.
        for path in sorted(MENUS_DIR.glob("*.json")):
            brand_id = path.stem.upper()
            config = MenuConfig.model_validate_json(path.read_text())
            summary = apply_menu_config(session, brand_id, config)
            session.commit()
            print(f"{brand_id}: {summary}")


if __name__ == "__main__":
    seed(only_if_empty="--if-empty" in sys.argv)
