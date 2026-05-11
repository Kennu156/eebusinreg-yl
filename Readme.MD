# 🇪🇪 Estonian Business Registry Importer

A fast, robust CLI tool for downloading, importing, and querying the Estonian Business Registry (äriregister) open dataset.

---

## 📦 Installation

Requires Python 3.10+.

```bash
git clone <repo-url>
cd estonian-business-registry
pip install -r requirements.txt
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

## 📋 JSON Importer (`import_json.py`)

The JSON importer loads the **detailed** Estonian Business Registry dataset — a separate, richer export that includes contacts, business activities, capital history, and annual report metadata. The JSON is ~4.2 GB uncompressed, so it is streamed record-by-record using `ijson` without loading the whole file into memory.

### Prerequisites

Download the ZIP manually from the [Estonian Business Registry open data portal](https://avaandmed.ariregister.rik.ee/) and place it in the project root:

```
ettevotja_rekvisiidid__yldandmed.json.zip
```

### Run

```bash
python3 import_json.py
```

Uses the same `REGISTRY_DB` environment variable (default: `registry.duckdb`) as the main importer.

### Tables created

| Table | Source field | Description |
|---|---|---|
| `companies_detail` | `yldandmed` | Scalar fields per company (status, legal form, region, etc.) |
| `contacts` | `sidevahendid` | Email, phone, web per company |
| `activities` | `teatatud_tegevusalad` | EMTAK business activity codes |
| `capitals` | `kapitalid` | Share capital history |
| `annual_reports` | `info_majandusaasta_aruannetest` | Annual report metadata including employee count |

Re-running is fully idempotent: existing rows are deleted and re-inserted from the ZIP on each run.

### Example queries after import

```sql
-- Active companies with their primary EMTAK activity
SELECT cd.nimi, a.emtak_tekstina
FROM companies_detail cd
JOIN activities a ON a.ariregistri_kood = cd.ariregistri_kood
WHERE cd.staatus = 'R' AND a.on_pohitegevusala = true
LIMIT 20;

-- Companies by contact type
SELECT liik_tekstina, COUNT(*) FROM contacts GROUP BY liik_tekstina ORDER BY 2 DESC;
```

## 📁 Project Structure

```
/
├── importer.py          ← Entry point (CSV dataset)
├── import_json.py       ← Standalone JSON dataset importer
├── requirements.txt     ← Python dependencies
├── README.md
└── src/
    ├── __init__.py
    ├── cli.py           ← Click commands (import, stats, search, get, export)
    ├── db.py            ← DuckDB schema, import logic, queries
    ├── downloader.py    ← Streaming download + ZIP extraction
    └── output.py        ← Table / JSON / CSV formatters
```

## 📄 Data License

The source data is published by the [Estonian Business Registry](https://avaandmed.ariregister.rik.ee/) under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). This tool downloads the data live at runtime and does not redistribute it.