"""Interactive helper to hit the dish-mgmt API endpoints.

Asks which endpoint to call and for any inputs it needs, then prints the
response. The server URL comes from DISH_API_URL (default http://127.0.0.1:8000).

    .venv/bin/python scripts/dish_api.py
"""

import json
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("DISH_API_URL", "http://127.0.0.1:8000").rstrip("/")

# key -> (label, method, path, needs_brand, needs_body, writes)
ENDPOINTS = {
    "1": ("GET  /v1/health", "GET", "/v1/health", False, False, False),
    "2": ("GET  /v1/menus/db", "GET", "/v1/menus/db", True, False, False),
    "3": ("POST /v1/menus/validate", "POST", "/v1/menus/validate", True, True, False),
    "4": ("PUT  /v1/menus/db", "PUT", "/v1/menus/db", True, True, True),
}


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default


def load_body(brand_id: str) -> dict:
    default = REPO_ROOT / "menus" / f"{brand_id.lower()}.json"
    path = Path(
        ask("Path to menu JSON file", str(default) if default.is_file() else None)
    ).expanduser()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.exit(f"Could not read JSON from {path}: {exc}")


def main() -> int:
    print(f"Server: {BASE_URL}\n\nWhich endpoint?")
    for key, entry in ENDPOINTS.items():
        print(f"  {key}) {entry[0]}")
    choice = ask("Number")
    if choice not in ENDPOINTS:
        sys.exit(f"Invalid choice: {choice}")
    _, method, path, needs_brand, needs_body, writes = ENDPOINTS[choice]

    params: dict[str, str] = {}
    if needs_brand:
        params["brandId"] = ask("brandId", "SUNRISE").upper()
    body = load_body(params["brandId"]) if needs_body else None

    if writes and ask(f"Save to the database for {params['brandId']}? (y/n)", "n").lower() != "y":
        print("Cancelled.")
        return 0

    url = f"{BASE_URL}{path}"
    print(f"\n{method} {url} params={params}")
    try:
        resp = httpx.request(method, url, params=params, json=body, timeout=60)
    except httpx.ConnectError:
        sys.exit(f"Could not connect to {BASE_URL}. Is the server running?")
    print(f"HTTP {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2))
    except ValueError:
        print(resp.text)
    return 0 if resp.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
