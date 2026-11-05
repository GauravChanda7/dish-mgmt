import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import database, models
from app.main import app
from app.seed import seed

ROOT = Path(__file__).resolve().parent.parent
SUNRISE = json.loads((ROOT / "menus" / "sunrise.json").read_text())
HARBOR = json.loads((ROOT / "menus" / "harbor.json").read_text())


@pytest.fixture()
def session_factory():
    """A fresh in-memory database, seeded, for every test."""
    engine = database.make_engine(
        "sqlite://", poolclass=StaticPool
    )  # one shared in-memory connection
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed(engine=engine, session_factory=factory)

    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[database.get_db] = override
    yield factory
    app.dependency_overrides.clear()


@pytest.fixture()
def client(session_factory):
    # No `with`, so the lifespan (which uses the real engine) is not run.
    return TestClient(app)


def count(session_factory, model):
    with session_factory() as s:
        return s.scalar(select(func.count()).select_from(model))


# ---- health ---------------------------------------------------------------


def test_health(client):
    assert client.get("/v1/health").json() == {"status": "ok"}


# ---- GET ------------------------------------------------------------------


@pytest.mark.parametrize("brand,expected", [("SUNRISE", SUNRISE), ("HARBOR", HARBOR)])
def test_get_round_trips_the_sample_file(client, brand, expected):
    response = client.get("/v1/menus/db", params={"brandId": brand})
    assert response.status_code == 200
    assert response.json() == expected


def test_get_is_case_insensitive_and_empty_brand_is_empty(client):
    assert client.get("/v1/menus/db", params={"brandId": "sunrise"}).status_code == 200
    body = client.get("/v1/menus/db", params={"brandId": "ORCHARD"}).json()
    assert body["dishes"] == body["menus"] == body["rules"] == []


def test_get_unknown_brand_is_404(client):
    assert client.get("/v1/menus/db", params={"brandId": "NOPE"}).status_code == 404


def test_get_requires_brand_id(client):
    assert client.get("/v1/menus/db").status_code == 422


# ---- validate -------------------------------------------------------------


def test_validate_good_file_saves_nothing(client, session_factory):
    changed = deepcopy(SUNRISE)
    changed["dishes"][0]["price"] = "9.99"
    before = count(session_factory, models.MenuRule)
    response = client.post(
        "/v1/menus/validate", params={"brandId": "SUNRISE"}, json=changed
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["would_change"]["dishes"]["updated"] == 1
    assert count(session_factory, models.MenuRule) == before
    # still the old price
    got = client.get("/v1/menus/db", params={"brandId": "SUNRISE"}).json()
    assert got["dishes"][0]["price"] == "4.25"


@pytest.mark.parametrize(
    "example,expected",
    [
        (
            "invalid_fields.json",
            ("colour", "must be 2 characters", "course", "sub-second"),
        ),
        (
            "invalid_references.json",
            ("Ghost Dish", "more than one featured dish", "Weekend Menu", "no catch-all"),
        ),
    ],
)
def test_validate_reports_every_problem_in_the_examples(client, example, expected):
    invalid = json.loads((ROOT / "examples" / example).read_text())
    response = client.post(
        "/v1/menus/validate", params={"brandId": "SUNRISE"}, json=invalid
    )
    assert response.status_code == 422
    text = json.dumps(response.json())
    for item in expected:
        assert item in text, item


def test_validate_unknown_brand_is_404(client):
    response = client.post("/v1/menus/validate", params={"brandId": "NOPE"}, json=HARBOR)
    assert response.status_code == 404


def test_validate_warns_on_duplicate_rule_key(client):
    config = deepcopy(HARBOR)
    config["rules"].append(deepcopy(config["rules"][0]))
    config["rules"][-1]["menu"] = "Late Night Menu"
    body = client.post(
        "/v1/menus/validate", params={"brandId": "HARBOR"}, json=config
    ).json()
    assert body["valid"] is True
    assert len(body["warnings"]) == 1


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda c: c.update(schema_version="6"), "schema_version"),
        (lambda c: c["dishes"].append(deepcopy(c["dishes"][0])), "duplicate dish"),
        (lambda c: c["menus"].append(deepcopy(c["menus"][0])), "duplicate menu"),
        (lambda c: c["dishes"][0].update(course="TOOLONG"), "course"),
        (lambda c: c["dishes"][0].update(price=1.234), "price"),
        (lambda c: c["rules"][0].pop("priority"), "priority"),
        (lambda c: c["rules"].__setitem__(slice(None), c["rules"][1:]), "catch-all"),
    ],
)
def test_validate_rejects(client, mutate, expected):
    config = deepcopy(HARBOR)
    mutate(config)
    response = client.post(
        "/v1/menus/validate", params={"brandId": "HARBOR"}, json=config
    )
    assert response.status_code == 422
    assert expected in json.dumps(response.json())


# ---- PUT ------------------------------------------------------------------


def test_put_updates_and_get_returns_it(client):
    changed = deepcopy(SUNRISE)
    changed["dishes"][0]["price"] = "9.99"
    changed["dishes"][0]["texts"]["fr"] = "Cafe glace."
    response = client.put("/v1/menus/db", params={"brandId": "SUNRISE"}, json=changed)
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert client.get("/v1/menus/db", params={"brandId": "SUNRISE"}).json() == changed


def test_put_never_deletes_dishes_or_menus(client, session_factory):
    smaller = deepcopy(SUNRISE)
    smaller["dishes"] = [d for d in smaller["dishes"] if d["uid"] != "Lemon Tart"]
    smaller["menus"] = [g for g in smaller["menus"] if g["uid"] == "Weekday Menu"]
    smaller["rules"] = [r for r in smaller["rules"] if r["menu"] == "Weekday Menu"]
    dishes_before = count(session_factory, models.Dish)
    menus_before = count(session_factory, models.Menu)
    assert (
        client.put(
            "/v1/menus/db", params={"brandId": "SUNRISE"}, json=smaller
        ).status_code
        == 200
    )
    assert count(session_factory, models.Dish) == dishes_before
    assert count(session_factory, models.Menu) == menus_before
    # the export is built from the whole DB, so the left-out rows are still there
    got = client.get("/v1/menus/db", params={"brandId": "SUNRISE"}).json()
    assert "Lemon Tart" in [d["uid"] for d in got["dishes"]]
    assert "Holiday Menu" in [g["uid"] for g in got["menus"]]
    # but the rules are exactly the new set: old ones were expired
    assert [r["menu"] for r in got["rules"]] == ["Weekday Menu"]


def test_put_expires_old_rules_and_keeps_the_rows(client, session_factory):
    before = count(session_factory, models.MenuRule)
    client.put("/v1/menus/db", params={"brandId": "SUNRISE"}, json=SUNRISE)
    after = count(session_factory, models.MenuRule)
    assert after == before + len(SUNRISE["rules"])  # old rows kept, new rows added
    got = client.get("/v1/menus/db", params={"brandId": "SUNRISE"}).json()
    assert got == SUNRISE  # only the live copies are exported


def test_put_is_idempotent(client):
    for _ in range(2):
        assert (
            client.put("/v1/menus/db", params={"brandId": "HARBOR"}, json=HARBOR).status_code
            == 200
        )
    assert client.get("/v1/menus/db", params={"brandId": "HARBOR"}).json() == HARBOR


def test_put_into_empty_brand_and_brands_are_isolated(client):
    assert (
        client.put("/v1/menus/db", params={"brandId": "ORCHARD"}, json=HARBOR).status_code
        == 200
    )
    # same dish names for two brands are two rows, and HARBOR is untouched
    assert client.get("/v1/menus/db", params={"brandId": "ORCHARD"}).json() == HARBOR
    assert client.get("/v1/menus/db", params={"brandId": "HARBOR"}).json() == HARBOR


def test_put_invalid_changes_nothing(client, session_factory):
    bad = deepcopy(HARBOR)
    bad["menus"][0]["dishes"].append("Ghost Dish")
    before = count(session_factory, models.Dish)
    assert (
        client.put("/v1/menus/db", params={"brandId": "HARBOR"}, json=bad).status_code
        == 422
    )
    assert count(session_factory, models.Dish) == before


def test_put_unknown_brand_is_404_and_changes_nothing(client, session_factory):
    before = count(session_factory, models.Dish)
    assert (
        client.put("/v1/menus/db", params={"brandId": "NOPE"}, json=HARBOR).status_code
        == 404
    )
    assert count(session_factory, models.Dish) == before


def test_expired_rules_are_not_exported(client, session_factory):
    from datetime import datetime

    with session_factory() as s:
        menu = s.scalar(select(models.Menu).where(models.Menu.name == "All Day Menu"))
        s.add(
            models.MenuRule(
                menu_id=menu.id,
                region="OLD",
                channel=None,
                priority=9,
                active_date=None,
                expire_date=datetime(2020, 1, 1),
            )
        )
        s.commit()
    got = client.get("/v1/menus/db", params={"brandId": "HARBOR"}).json()
    assert "OLD" not in [r["region"] for r in got["rules"]]
