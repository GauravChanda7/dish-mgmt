# Mock schema: bakery franchise menus

A small stand-in for the RBO offer tables, to be rebuilt by hand in ServiceNow. It keeps the same shapes and edge cases as `offer-json-to-db-mapping.md`, `schema.txt` and `result.md`, with 6 tables and 24 columns instead of 9+ tables and about 75 columns. The domain shares nothing with the original.

## Contents

1. [Design summary](#1-design-summary), including [how the tables connect in plain words](#11-how-the-tables-connect-in-plain-words)
2. [Concept map](#2-concept-map)
3. [Tables](#3-tables)
4. [JSON shape](#4-json-shape)
5. [JSON to tables](#5-json--tables)
6. [Tables to JSON](#6-tables--json)
7. [Edge cases kept](#7-edge-cases-kept)
8. [What was dropped](#8-what-was-dropped)
9. [Sample data](#9-sample-data)
10. [ServiceNow build notes](#10-servicenow-build-notes)

---

## 1. Design summary

A **brand** (a bakery franchise) owns **dishes**. Each dish has descriptions per language. Dishes are bundled into **menus**. **Menu rules** decide which menu applies for a given region and channel, with a priority and a date window.

One JSON file per brand: `menus/<brand>.json`. The filename is the brand ID, as in `offers/<partner>.json`.

Six tables is the smallest set that keeps every relationship shape in the original: scoping, a main entity, a child dict, a many-to-many link, a named group, and wildcard rules pointing at the group.

### 1.1 How the tables connect, in plain words

Think of it as a bakery chain that has several brands, and each brand sells its own dishes.

**The cast**

- **`brand`** is the owner, for example SUNRISE or HARBOR. Everything else belongs to one brand. It's like a customer ID that scopes all the other tables.
- **`dish`** is a thing you can sell, like "Morning Combo", with a price, a course and a featured flag. Each dish belongs to one brand through `brandId`.
- **`dishText`** holds the description of a dish in each language. "Morning Combo" has one row for English and one for Spanish. It's a small side table, with one row per dish and language.
- **`menu`** is a named bundle of dishes, like "Weekday Menu". It also belongs to one brand.
- **`menuDish`** answers "which dishes are on which menu?". A menu has many dishes and a dish can sit on many menus, so this table just pairs the two IDs and holds nothing else. For example, Sourdough Loaf is on both the Weekday and Holiday menus.
- **`menuRule`** decides which menu is shown to a customer. A rule says "in region X, on channel Y, show this menu", with a priority and a start and end date. Each rule points at one menu.

**How they hang together**

```
brand ──owns──► dish ──has──► dishText   (descriptions per language)
  │               ▲
  │               └── menuDish ──┐        (the pairing table)
  └──owns──► menu ◄──────────────┘
               ▲
               └── menuRule              (which menu applies, when and where)
```

Two chains do the work:

1. **Brand to dish to text:** a brand owns dishes, and each dish carries its translations.
2. **Brand to menu to rule:** a brand owns menus, and rules decide when each menu applies.

`menuDish` is the bridge between the two chains, since it says which dishes go on which menu. A rule has no brand column of its own. It finds its brand by following its menu to that menu's brand.

**Which table answers which question**

| Question | Table |
|---|---|
| Who owns this? | `brand` |
| What can be sold, and for how much? | `dish` |
| What is it called in Spanish? | `dishText` |
| What menus exist? | `menu` |
| What is on this menu? | `menuDish` |
| Which menu does this customer get? | `menuRule` |

**A walk-through for SUNRISE** (the data in `menus/sunrise.json`)

1. A customer in Seattle (`SEA`) orders for delivery on 22 December 2026.
2. SUNRISE's rules are checked. Only rules whose date window includes that day count. Here the Holiday rules start on 20 December.
3. Three rules match: the catch-all (priority 1), "SEA, any channel" (priority 3) and "SEA + delivery" (priority 5). The highest priority wins, so "SEA + delivery" is used.
4. That rule points at the Holiday Menu.
5. `menuDish` lists the dishes on the Holiday Menu: Iced Latte, Lemon Tart and Sourdough Loaf.
6. Each dish's name and price come from `dish`, and its description in the customer's language comes from `dishText`.

A null `region` or `channel` on a rule means "match anything". A rule with both null is the catch-all, so every customer gets some menu. A customer in Portland on delivery would skip the SEA rules and get the catch-all menu, the Weekday Menu.

The backend only stores and returns this configuration. It does not pick a menu for a customer. The matching above is how the rules are meant to be read.

**How the JSON lines up**

The JSON file for a brand mirrors this:
- `dishes` hold the dish rows, with their `texts` nested inside.
- `menus` list dish names, which is the `menuDish` pairing written as a list.
- `rules` name a menu.

Names are the glue in the file, because the IDs only exist in the database. The `id` columns on `dish`, `menu` and `menuRule` are generated by the database. The JSON never contains them. They're resolved from names when a file is loaded.

**Things that may surprise you**

- Dish and menu names only need to be unique within a brand. SUNRISE and HARBOR can each have a dish called "Morning Combo", and they are two separate rows.
- A menu can end up holding a dish from another brand if the database was edited by hand. The export hides it.
- Removing a dish or menu from a file does not delete it from the database.
- Rules are the odd one out: they have no name to match on, so every load expires the live rules and inserts the file's rules as new rows (edge case 17).

## 2. Concept map

| Mock table | Original equivalent | Role |
|---|---|---|
| `brand` | `customer` (not in `schema.txt`) | Owner of everything. Read-only. The "partnerId" source. |
| `dish` | `serviceTier` | Main entity: scalars, a bool, a short code and a JSON blob. |
| `dishText` | `serviceTierDescription` | Child rows keyed by language. Becomes a dict. |
| `menu` | `serviceTierGroup` | Named bundle of dishes. |
| `menuDish` | `serviceTierMapping` | Pure link table: menu to dish. |
| `menuRule` | `serviceTierRules` | Wildcard matching, priority, dates, points at a menu. Its brand comes from the menu. |

Foreign keys:

```
brand.brandId ◄── dish.brandId
brand.brandId ◄── menu.brandId

dish ◄── dishText.dishId
dish ◄── menuDish.dishId ──► menu ◄── menuRule.menuId
```

References inside the JSON, by name:

```
dishes[].uid  <──  menus[].dishes[]
menus[].uid   <──  rules[].menu
```

## 3. Tables

Types are given in MySQL terms, so they can be compared with `schema.txt`. Pick the closest ServiceNow field type when you build them (section 10).

### 3.1 `brand` (read-only, the partnerId equivalent)

| Column | Type | Null | Notes |
|---|---|---|---|
| `brandId` | varchar(32) | NO | **PK.** Uppercase, e.g. `SUNRISE`. Equals the filename uppercased. |
| `currency` | varchar(3) | NO | e.g. `USD`. Never written by the tool. |

### 3.2 `dish`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | int unsigned | NO | auto | **PK.** |
| `brandId` | varchar(32) | NO | | FK to `brand`. Natural key is (`brandId`, `name`). |
| `name` | varchar(50) | NO | | JSON `uid`. |
| `price` | decimal(8,2) | NO | 0.00 | JSON string such as `"4.50"`. |
| `featured` | bit(1) | NO | b'0' | JSON bool. Works like `autoConnect`. |
| `course` | varchar(5) | YES | | Short code: `MAIN`, `DSRT`, `DRINK`. Works like `deviceCategoryName`. |
| `extra` | longtext | YES | | JSON object text. Keys are whitelisted. Works like `metadata`. |

### 3.3 `dishText`

| Column | Type | Null | Notes |
|---|---|---|---|
| `dishId` | int unsigned | NO | **PK part 1.** FK to `dish.id`. |
| `langCode` | varchar(2) | NO | **PK part 2.** |
| `text` | varchar(2048) | YES | |

### 3.4 `menu`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | int unsigned | NO | **PK**, auto. |
| `brandId` | varchar(32) | NO | FK to `brand`. Natural key is (`brandId`, `name`). |
| `name` | varchar(250) | NO | JSON `uid`. |

### 3.5 `menuDish`

| Column | Type | Null | Notes |
|---|---|---|---|
| `menuId` | int unsigned | NO | **PK part 1.** FK to `menu.id`. |
| `dishId` | int unsigned | NO | **PK part 2.** FK to `dish.id`. |

Nothing else. This table only connects the other two.

### 3.6 `menuRule`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | int | NO | auto | **PK.** |
| `menuId` | int unsigned | NO | | FK to `menu.id`. |
| `region` | varchar(10) | YES | | Null means match any. |
| `channel` | varchar(24) | YES | | `DINEIN`, `DELIVERY`, and so on. Null means match any. |
| `priority` | tinyint unsigned | NO | 1 | |
| `activeDate` | timestamp | YES | | UTC. |
| `expireDate` | timestamp | YES | | UTC. Null means no expiry. |

## 4. JSON shape

```
{
  "schema_version": "1",
  "dishes": [
    {
      "uid": "Morning Combo",
      "price": "6.50",
      "featured": true,
      "course": "MAIN",
      "extra": {"badge": "Fresh", "note": "Served until 11am"},
      "texts": {"en": "Coffee and a croissant.", "es": "Cafe y croissant."}
    }
  ],
  "menus": [
    {"uid": "Weekday Menu", "dishes": ["Morning Combo", "Sourdough Loaf"]}
  ],
  "rules": [
    {
      "menu": "Weekday Menu",
      "region": null,
      "channel": null,
      "priority": 1,
      "active_date": "2026-01-01T00:00:00+00:00",
      "expire_date": null
    }
  ]
}
```

Ordering, matching the real files:
- Root keys in the order `schema_version`, `dishes`, `menus`, `rules`.
- Everything nested is sorted alphabetically, with `uid` first.
- Dishes and menus are sorted by `uid`. A menu's `dishes` are sorted alphabetically.
- Rules are sorted by (`priority`, `region`, `channel`, `active_date`, `expire_date`).
- 4-space indent.

## 5. JSON to tables

Run in one transaction, in this order (same shape as `create_or_update_offer_configuration`):

| # | Step | Table | Behaviour |
|---|---|---|---|
| 1 | Resolve brand | `brand` | Fails with "brand not found" if `brandId` is missing. Read-only. |
| 2 | Dishes | `dish` | Match on `brandId` + `name`. Insert new, update changed. **Never delete.** |
| 3 | Texts | `dishText` | Delete all rows of the file's dishes, then insert the file's. |
| 4 | Menus | `menu` | Match on `brandId` + `name`. Insert or update. **Never delete.** |
| 5 | Menu members | `menuDish` | Delete all rows of the file's menus, then insert the file's. |
| 6 | Rules | `menuRule` | Set `expireDate = NOW()` on all live rules (no expiry, or expiry in the future) whose menu belongs to the brand. Then insert every rule in the file as a new row. **Never delete.** |

Field mapping:

| JSON | Column | Notes |
|---|---|---|
| `dishes[].uid` | `dish.name` | |
| `dishes[].price` | `dish.price` | String in JSON. |
| `dishes[].featured` | `dish.featured` | Bool to bit. |
| `dishes[].course` | `dish.course` | Max 5 characters. |
| `dishes[].extra` | `dish.extra` | Written as JSON text. `{}` when empty. |
| `dishes[].texts{lang: text}` | `dishText(dishId, langCode, text)` | One row per entry. |
| `menus[].uid` | `menu.name` | |
| `menus[].dishes[]` | `menuDish(menuId, dishId)` | One row per dish name. |
| `rules[].menu` | `menuRule.menuId` | Looked up by menu name among this brand's menus. |
| `rules[].region` / `channel` | `region` / `channel` | Null stays null. |
| `rules[].priority` | `priority` | |
| `rules[].active_date` / `expire_date` | `activeDate` / `expireDate` | Converted to UTC. |
| *(filename)* | `brandId` on `dish` and `menu` | Rules get their brand through `menuId`. |

## 6. Tables to JSON

`:B` is the brand ID.

```sql
-- dishes
SELECT id, name AS uid, price, (featured + 0) = 1 AS featured, course, extra
FROM dish WHERE brandId = :B;

-- texts
SELECT d.id AS dish_id, t.langCode, t.text
FROM dish d JOIN dishText t ON t.dishId = d.id
WHERE d.brandId = :B;

-- menus and their dishes (LEFT JOIN so an empty menu still appears)
SELECT m.id AS menu_id, m.name AS uid, d.name AS dish
FROM menu m
LEFT JOIN menuDish md ON md.menuId = m.id
LEFT JOIN dish d ON d.id = md.dishId AND d.brandId = :B
WHERE m.brandId = :B;

-- rules (live and future only)
SELECT m.name AS menu, r.region, r.channel, r.priority,
       r.activeDate AS active_date, r.expireDate AS expire_date
FROM menuRule r JOIN menu m ON m.id = r.menuId
WHERE m.brandId = :B AND (r.expireDate IS NULL OR r.expireDate > NOW());
```

Build rules:
- `price` is written as a string (`"6.50"`).
- `extra` is parsed from text. A null or empty value becomes `{}`. Invalid JSON text is an error that names the dish ID.
- `featured` is a real bool.
- Timestamps are naive UTC in the DB. Add `+00:00` when writing.
- Expired rules are left out of the export. They stay in the table.
- A menu can contain another brand's dish. The export keeps only this brand's dishes.
- A rule belongs to the brand that owns its menu, so a rule can never point at another brand's menu.

## 7. Edge cases kept

Each item maps to something real in the original, so the same kinds of bugs and checks can be tested.

**Record and field checks** (`result.md` section A)

| # | Edge case | Original |
|---|---|---|
| 1 | Required fields and types: `price` is a decimal, `featured` is a bool, `priority` is a required int on a rule. | A1 |
| 2 | `schema_version` must equal `"1"`. | A2 |
| 3 | Dates are ISO-8601 with no sub-second part. A naive date is treated as UTC. | A3 |
| 4 | `extra` may only use these keys: `badge` (string), `note` (string), `imageKey` (string), `vegan` (bool). Unknown keys are rejected. | A4 |
| 5 | `course` max 5 characters and `langCode` max 2 characters. A longer value fits no column. | varchar(5) and varchar(2) |
| 6 | `price` is a string in JSON but a decimal in the DB. A number in JSON fails or loses its trailing zero. | `cost` |
| 7 | `extra` and `course` may be null in the DB. `extra` must come out as `{}`. `texts` may be empty. | `metadata`, `descriptions` |

**Cross-record checks** (`result.md` section B)

| # | Edge case | Original |
|---|---|---|
| 8 | Dish names are unique within a file. | B1 |
| 9 | Menu names are unique within a file. | B2 |
| 10 | Every dish named in a menu exists in `dishes`. | B3 |
| 11 | Every rule's `menu` exists in `menus`. | B4 |
| 12 | A menu may not contain two featured dishes of the same course. | B7 |
| 13 | Catch-all rule: after ranking, a rule with `region` and `channel` both null must exist. | B8 |
| 14 | Brand must exist in `brand`. | B10 |

**Load behaviour** (`offer-json-to-db-mapping.md` section 5)

| # | Edge case | Original |
|---|---|---|
| 15 | Removing a dish from the file does not delete it. | tiers never deleted |
| 16 | Removing a menu from the file does not delete it. | groups never deleted |
| 17 | Any change to a rule expires all live rules of the brand's menus and inserts the whole set again. Old rows stay with `expireDate` = now. | rules |
| 18 | Texts and menu members are deleted and re-inserted, even when nothing changed. | description and mapping tables |
| 19 | Two brands can have a dish with the same name. They are two rows. | partner scoping |
| 20 | A rule with every match field null matches anything. A rule with only some set is narrower. Higher `priority` wins when two rules match. | rules |
| 21 | The same match key pointing at two different menus is a warning, not an error. | duplicate-rule warning |
| 22 | The DB export drops expired rules, so a re-import round trip must show no diff. | C5 |
| 23 | Sorting must not change the data. Round trip JSON, DB, JSON shows no diff. | C1, C5 |

## 8. What was dropped

| Dropped | Why it is safe |
|---|---|
| `npiCode` table and `npi_codes` list | Its shape (lookup by name, foreign key from the main entity) is already covered by menu to dish and rule to menu. Adds a table and a root list. |
| `serviceTierTag` and `serviceTierTagMapping` | The "list of strings" shape is still covered by `menus[].dishes`. The global shared tag table and tag-reversal behaviour are lost. |
| `vehicle` | There is no third match column. The "unknown vehicle fails on foreign key" case is lost. `region` and `channel` still cover partial wildcard matching. |
| `rules` having their own partner ID | The original repeats `partnerId` on rules. Here a rule gets its brand from its menu. A rule pointing at another brand's menu can't occur, so that edge case is lost. |
| Extra scalar columns (`prepMinutes`, `stock`) | Same shape as `price` (number) and `priority` (int). |
| Infra tiers and partner extras | These are injected by code, not stored data. Not needed to test the DB and JSON mapping. |
| `alternateCost`, `deviceTypeMask`, `signupLimit`, `duration`, timestamps, `active` | Same shape as the columns kept. |
| `flightNumber`, `cpExceptionsId` | Same shape as the nullable match columns kept. |

## 9. Sample data

`brand`

| brandId | currency |
|---|---|
| SUNRISE | USD |
| HARBOR | CAD |

`dish` (SUNRISE)

| id | name | price | featured | course | extra |
|---|---|---|---|---|---|
| 1 | Morning Combo | 6.50 | 1 | MAIN | {"badge":"Fresh"} |
| 2 | Sourdough Loaf | 5.00 | 0 | MAIN | null |
| 3 | Lemon Tart | 3.75 | 1 | DSRT | {"note":"Contains nuts"} |

`dishText`

| dishId | langCode | text |
|---|---|---|
| 1 | en | Coffee and a croissant. |
| 1 | es | Cafe y croissant. |
| 3 | en | Tart with lemon curd. |

`menu` (SUNRISE)

| id | name |
|---|---|
| 1 | Weekday Menu |
| 2 | Holiday Menu |

`menuDish`

| menuId | dishId |
|---|---|
| 1 | 1 |
| 1 | 2 |
| 2 | 2 |
| 2 | 3 |

`menuRule` (SUNRISE menus)

| id | menuId | region | channel | priority | activeDate | expireDate |
|---|---|---|---|---|---|---|
| 1 | 1 | null | null | 1 | 2026-01-01 00:00:00 | null |
| 2 | 2 | SEA | DELIVERY | 5 | 2026-12-20 00:00:00 | 2027-01-02 00:00:00 |
| 3 | 2 | SEA | null | 3 | 2026-12-20 00:00:00 | null |

Rule 1 is the catch-all. Rule 2 beats rule 3 where both match (Seattle delivery), because of its higher priority. This sample has one featured dish per course per menu, so check 12 passes.

## 10. ServiceNow build notes

Build order, because of the references: `brand`, `dish`, `menu`, `dishText`, `menuDish`, `menuRule`.

| Mock type | Suggested ServiceNow field |
|---|---|
| varchar(n) | String, max length n |
| int, bigint | Integer, Long |
| decimal(8,2) | Decimal or Currency |
| bit(1) | True/False |
| text, longtext | String (up to 4000) or JSON field |
| timestamp | Date/Time |
| FK | Reference to the table |
| `brandId` | Reference to `brand`, or a plain string if you want to keep it close to the original |

- `dishText` and `menuDish` have composite keys. In ServiceNow use a unique index over both columns.
- Uniqueness of (`brandId`, `name`) on `dish` and `menu` must be set as a unique index. The original relies on code for it.
- `menuRule` has no natural key, on purpose (edge case 17).
