# dish-mgmt

A small FastAPI + SQLAlchemy backend that stores a brand's menu config (JSON) in a database and serves it back. It is a simplified copy of the offer-management API. The table design is in `../ifc-offer-management-tool/new_schema.md`.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m app.seed                       # create tables, load menus/*.json
.venv/bin/uvicorn app.main:app --reload            # http://127.0.0.1:8000/docs
.venv/bin/python -m pytest                         # 26 tests, in-memory DB
```

`app.seed` is safe to re-run. To start over, delete `dish_mgmt.db` and seed again.

## Endpoints

The brand goes in the `brandId` query parameter (case-insensitive), like `partnerId` in the original.

| Endpoint | Original | What it does |
|---|---|---|
| `GET /v1/health` | `GET /v1/health` | `{"status": "ok"}` |
| `GET /v1/menus/db?brandId=SUNRISE` | `GET /v1/offers/rbo` | Builds the brand's JSON from the tables. Unknown brand: 404. |
| `POST /v1/menus/validate?brandId=SUNRISE` | `POST /v1/offers/validate` | Checks the body, runs the whole load, then rolls back. Returns warnings and what would change. |
| `PUT /v1/menus/db?brandId=SUNRISE` | `PUT /v1/offers/rbo` | Checks the body and saves it in one transaction. |

To call them without typing curl, run the interactive helper (like `scripts/offerman_api.py` in the original). It lists the endpoints, asks for the brand and a JSON file, and prints the response. `PUT` asks for confirmation first. Set `DISH_API_URL` if the server is not on `http://127.0.0.1:8000`.

```bash
.venv/bin/python scripts/dish_api.py
```

A body that fails validation returns 422 with every problem listed. Examples to try with validate:

```bash
curl -X POST "localhost:8000/v1/menus/validate?brandId=SUNRISE" \
  -H 'content-type: application/json' -d @examples/invalid_references.json
```

- `examples/invalid_fields.json`: bad field values (unknown `extra` key, 3-letter language code, long `course`, sub-second date).
- `examples/invalid_references.json`: bad cross-record rules (unknown dish, unknown menu, two featured dishes in one course, no catch-all rule). These only run once the fields parse, so they are in a separate file.

There is no authentication, unlike the original.

## Sample data

- `menus/sunrise.json` and `menus/harbor.json`: the filename is the brand, as with `offers/<partner>.json`. The files are what `GET` returns after seeding.
- Brands `SUNRISE`, `HARBOR` and `ORCHARD` are created by the seed. `ORCHARD` has no file, so its `GET` is empty. Use it to try a `PUT`.

## Behaviour carried over from the original

- Dishes and menus are matched on brand + name. They are inserted or updated, never deleted. A dish left out of a file stays in the database.
- Texts and menu members are deleted and re-inserted on every load.
- Rules: every live rule of the brand's menus gets `expireDate = now`, then the file's rules are inserted as new rows. `GET` only returns live and future rules.
- A rule's brand comes from its menu.

## Moving to PostgreSQL or MySQL

Set `DATABASE_URL` and install the driver. No code changes.

```bash
pip install "psycopg[binary]"
DATABASE_URL=postgresql+psycopg://user:pw@host:5432/dish uvicorn app.main:app

pip install pymysql
DATABASE_URL=mysql+pymysql://user:pw@host:3306/dish uvicorn app.main:app
```

Notes:
- Models use only portable types. `extra` is `LONGTEXT` on MySQL and `TEXT` elsewhere. SQLite foreign keys are switched on with a pragma, since SQLite ignores them by default.
- Tables are created at startup with `create_all`. For production, replace that with Alembic migrations.
- I only ran this against SQLite. The PostgreSQL and MySQL paths are untested.
- Tests and the seed run with an in-memory SQLite database and the real code, so they show the logic works but not the other databases.

## Deploy to Render

`render.yaml` is a Blueprint that creates a **Web Service** (the API) and a **Postgres** database, and wires `DATABASE_URL` between them. SQLite is not suitable on Render because the service's disk is wiped on every deploy.

1. Push this folder to a GitHub or GitLab repo (`dish-mgmt` should be the repo root, or set the Blueprint's root directory).
2. In Render choose **New > Blueprint**, pick the repo, and apply.
3. On first start, `python -m app.seed --if-empty` creates the tables and loads `menus/*.json`. Later restarts skip loading, so rule rows don't pile up.
4. Check `https://<your-service>.onrender.com/v1/health`, then point the helper at it:

```bash
DISH_API_URL=https://<your-service>.onrender.com .venv/bin/python scripts/dish_api.py
```

Notes:
- The API has no authentication, so anyone with the URL can `PUT`. Add auth, or keep the service private, before putting real data in it.
- Free web services sleep when idle, so the first request after a pause is slow. Check Render's current terms for how long the free Postgres database lives.
- `render.yaml` has not been run on Render, and the PostgreSQL code path has only been checked for URL handling, not against a real PostgreSQL server.
