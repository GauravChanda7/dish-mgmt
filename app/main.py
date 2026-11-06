"""The FastAPI entrypoint.

    GET  /v1/health
    GET  /v1/brands                          list the brands
    GET  /v1/menus/db?brandId=SUNRISE        read a brand's menu config
    POST /v1/menus/validate?brandId=SUNRISE  validate and dry-run a config, nothing saved
    PUT  /v1/menus/db?brandId=SUNRISE        validate and save a config
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import database
from .models import Base, Brand
from .schemas import MenuConfig
from .service import BrandNotFound, apply_menu_config, get_menu_config


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fine for SQLite and demos. For production use migrations (Alembic).
    Base.metadata.create_all(database.engine)
    yield


app = FastAPI(title="dish-mgmt-api", version="1.0.0", lifespan=lifespan)

BrandId = Query(..., alias="brandId", description="The ID of the brand, e.g. SUNRISE")


@app.get("/v1/health")
def health():
    return {"status": "ok"}


@app.get("/v1/brands")
def v1_get_brands(db: Session = Depends(database.get_db)) -> list[dict]:
    """List every brand, like the partner-info service's GET /v1/partner."""
    brands = db.scalars(select(Brand).order_by(Brand.brand_id)).all()
    return [{"brandId": b.brand_id, "currency": b.currency} for b in brands]


@app.get("/v1/menus/db")
def v1_get_menus(
    brand_id: str = BrandId, db: Session = Depends(database.get_db)
) -> dict:
    """Get the menu config for a brand, built from the database."""
    brand_id = brand_id.upper()
    try:
        return get_menu_config(db, brand_id)
    except BrandNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/menus/validate")
def v1_validate_menus(
    config: MenuConfig, brand_id: str = BrandId, db: Session = Depends(database.get_db)
) -> dict:
    """Validate a menu config and run the full load against the database, then
    roll back. The body is checked by MenuConfig (a bad file returns 422)."""
    brand_id = brand_id.upper()
    try:
        changes = apply_menu_config(db, brand_id, config)
    except BrandNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        db.rollback()
    return {
        "valid": True,
        "brand_id": brand_id,
        "warnings": config.warnings(),
        "would_change": changes,
    }


@app.put("/v1/menus/db")
def v1_put_menus(
    config: MenuConfig, brand_id: str = BrandId, db: Session = Depends(database.get_db)
) -> dict:
    """Save a menu config for a brand. Everything is saved in one transaction."""
    brand_id = brand_id.upper()
    try:
        changes = apply_menu_config(db, brand_id, config)
        db.commit()
    except BrandNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return {
        "success": True,
        "brand_id": brand_id,
        "warnings": config.warnings(),
        "changes": changes,
    }
