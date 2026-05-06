# 🇪🇪 Estonian Business Registry Importer

A fast, robust CLI tool for downloading, importing, and querying the Estonian Business Registry (äriregister) open dataset (~370,000+ companies).

---

## ⚡ Performance (measured)

| Stage | Time | Records/sec |
|---|---|---|
| Import only (local CSV) | ~6–8 s | ~46,000–65,000/s |
| Full pipeline (download + import) | ~25–40 s | depends on network |

**Test hardware:** Ubuntu 24.04, Python 3.12, NVMe SSD  
**Record count:** 371,435

---

## 🗄️ Database Choice: DuckDB

**Why DuckDB over SQLite or PostgreSQL?**

- **Native CSV ingestion** — `read_csv()` is the fastest path from file to table; no Python-level row parsing needed
- **Columnar storage** — analytical queries (stats, county aggregations) are significantly faster
- **Proper `DATE` type** — dates are stored and returned as Python `date` objects, not strings
- **Zero configuration** — embedded, no server, single file
- **`INSERT OR REPLACE`** — makes re-imports fully idempotent without a separate upsert dance
- **`COPY TO`** — fast bulk CSV export directly from the engine

---

## 📦 Installation

Requires Python 3.10+.

```bash
git clone <repo-url>
cd estonian-business-registry
pip install -r requirements.txt
```

Make the entry point executable (optional):
```bash
chmod +x importer.py
```

---

## 🚀 Usage

### Import (download + insert)

```bash
python3 importer.py import
```

Fetches the ZIP from the official Estonian registry URL, extracts the CSV in-memory, and bulk-imports into `registry.duckdb`.

**Options:**
```
--db PATH       Path to DuckDB file (default: registry.duckdb, or REGISTRY_DB env var)
--url URL       Override the download URL (e.g. for testing)
--local PATH    Use a local CSV file instead of downloading
```

### Stats

```bash
python3 importer.py stats
```

Shows total record count, status breakdown with bar chart, top legal forms, and earliest registration date.

### Search

```bash
# By name (partial, case-insensitive)
python3 importer.py search --name "Tallinn"

# By status
python3 importer.py search --status "Registrisse kantud"

# By county
python3 importer.py search --county "Harju maakond"

# Combined filters
python3 importer.py search --name "Burger" --county "Tartu"

# Different output formats
python3 importer.py search --name "AS Hesburger" --format json
python3 importer.py search --name "AS Hesburger" --format csv

# Increase result limit
python3 importer.py search --county "Saare maakond" --limit 200
```

### Get (single company by code)

```bash
python3 importer.py get --code 11694365
python3 importer.py get --code 11694365 --format json
```

### Export filtered results to CSV

```bash
python3 importer.py export --county "Tartu maakond" --status "Registrisse kantud" -o tartu_active.csv
python3 importer.py export --name "OÜ" -o all_ou.csv
```

---

## 🌍 Environment Variables

| Variable | Default | Description |
|---|---|---|
| `REGISTRY_DB` | `registry.duckdb` | Path to the DuckDB database file |

Example:
```bash
REGISTRY_DB=/data/registry.duckdb python3 importer.py import
```

---

## 📁 Project Structure

```
/
├── importer.py          ← Entry point
├── requirements.txt     ← Python dependencies
├── README.md
└── src/
    ├── __init__.py
    ├── cli.py           ← Click commands (import, stats, search, get, export)
    ├── db.py            ← DuckDB schema, import logic, queries
    ├── downloader.py    ← Streaming download + ZIP extraction
    └── output.py        ← Table / JSON / CSV formatters
```

---

## 🛡️ Robustness

| Scenario | Behaviour |
|---|---|
| Network drops mid-download | `requests` timeout → clear error message, temp file cleaned up |
| Ctrl+C during download | Signal handler removes temp file, exits cleanly |
| Malformed CSV rows | DuckDB `ignore_errors=true` skips bad rows; valid rows still import |
| Re-running import | `INSERT OR REPLACE` — no duplicates, fully idempotent |
| DB doesn't exist yet | Auto-created on first `import` run |
| DB exists, `search`/`get` before import | Friendly error message with hint |
| Empty fields | Stored as `NULL` (not the string `"null"` or `""`) |
| BOM-prefixed UTF-8 | DuckDB handles automatically |
| Date parsing failures | `TRY_CAST` returns `NULL` instead of crashing |

---

## 🎯 Design Decisions

### Why not row-by-row insertion?
DuckDB's `read_csv()` + `CREATE TABLE AS SELECT` is a single vectorised operation — far faster than any Python loop inserting one row at a time.

### Why a staging table for upsert?
`INSERT OR REPLACE INTO target SELECT * FROM read_csv(...)` isn't directly supported in DuckDB. The staging table pattern lets us: (1) ingest the full CSV into memory in one shot, (2) do type coercion and NULL normalisation with SQL expressions, (3) upsert into the main table in one statement.

### Why stream the download?
The ZIP is 15–30 MB compressed. Streaming in 256 KB chunks means we never hold the full response body in memory at once, and the progress bar reflects actual bytes received.

### Date handling
`ettevotja_esmakande_kpv` arrives as `DD.MM.YYYY` strings. We use `strptime(..., '%d.%m.%Y')` inside DuckDB SQL wrapped in `TRY_CAST(... AS DATE)` — this gives us proper `DATE` columns with no Python-level date parsing overhead.

---

## 📄 Data License

The source data is published by the [Estonian Business Registry](https://avaandmed.ariregister.rik.ee/) under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). This tool downloads the data live at runtime and does not redistribute it.