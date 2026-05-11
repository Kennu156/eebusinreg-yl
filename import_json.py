"""
import_json.py — Stream-import the detailed Estonian Business Registry JSON dataset.

The JSON is 4.2 GB uncompressed — this uses ijson to parse it record-by-record
without loading the whole file into memory.

Tables created:
  companies_detail     — scalar fields from yldandmed (one row per company)
  contacts             — sidevahendid (email, phone, web per company)
  activities           — teatatud_tegevusalad (EMTAK business activities)
  capitals             — kapitalid (share capital history)
  annual_reports       — info_majandusaasta_aruannetest

Run:
    python3 import_json.py

Speed tips:
    Install the yajl C library for a 3-5x parsing boost:
        sudo apt install libyajl2   # Debian/Ubuntu
        pip install ijson[yajl2_c]  # or just: pip install ijson
    The script auto-selects the fastest available ijson backend.
"""

import importlib
import os
import time
import zipfile
from datetime import datetime

import duckdb
import ijson
from rich.console import Console
from rich.progress import MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

# Auto-select fastest available ijson backend (yajl2_c > yajl2_cffi > yajl2 > python)
_ijson_items = ijson.items
_ijson_backend = "python"
for _b in ("yajl2_c", "yajl2_cffi", "yajl2"):
    try:
        _mod = importlib.import_module(f"ijson.backends.{_b}")
        _ijson_items = _mod.items
        _ijson_backend = _b
        break
    except (ImportError, AttributeError):
        pass

console = Console()
DB_PATH = os.environ.get("REGISTRY_DB", "registry.duckdb")
ZIP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ettevotja_rekvisiidid__yldandmed.json.zip")
JSON_NAME = "ettevotja_rekvisiidid__yldandmed.json"

BATCH_SIZE = 5000

DDL = """
CREATE TABLE IF NOT EXISTS companies_detail (
    ariregistri_kood            VARCHAR PRIMARY KEY,
    nimi                        VARCHAR,
    ettevotteregistri_nr        VARCHAR,
    esmaregistreerimise_kpv     DATE,
    kustutamise_kpv             DATE,
    staatus                     VARCHAR,
    staatus_tekstina            VARCHAR,
    piirkond                    INTEGER,
    piirkond_tekstina           VARCHAR,
    piirkond_tekstina_pikk      VARCHAR,
    oiguslik_vorm               VARCHAR,
    oiguslik_vorm_tekstina      VARCHAR,
    oigusliku_vormi_alaliik     VARCHAR,
    on_raamatupidamiskohustuslane BOOLEAN,
    tegutseb_tekstina           VARCHAR,
    esitab_kasusaajad           BOOLEAN
);

CREATE TABLE IF NOT EXISTS contacts (
    kirje_id        BIGINT PRIMARY KEY,
    ariregistri_kood VARCHAR,
    liik            VARCHAR,
    liik_tekstina   VARCHAR,
    sisu            VARCHAR,
    lopp_kpv        DATE
);

CREATE TABLE IF NOT EXISTS activities (
    kirje_id            BIGINT PRIMARY KEY,
    ariregistri_kood    VARCHAR,
    emtak_kood          VARCHAR,
    emtak_tekstina      VARCHAR,
    nace_kood           VARCHAR,
    on_pohitegevusala   BOOLEAN,
    algus_kpv           DATE,
    lopp_kpv            DATE
);

CREATE TABLE IF NOT EXISTS capitals (
    kirje_id            BIGINT PRIMARY KEY,
    ariregistri_kood    VARCHAR,
    kapitali_suurus     DECIMAL(18,2),
    kapitali_valuuta    VARCHAR,
    algus_kpv           DATE,
    lopp_kpv            DATE
);

CREATE TABLE IF NOT EXISTS annual_reports (
    kirje_id                        BIGINT PRIMARY KEY,
    ariregistri_kood                VARCHAR,
    majandusaasta_perioodi_algus_kpv DATE,
    majandusaasta_perioodi_lopp_kpv  DATE,
    tootajate_arv                   INTEGER,
    tegevusala_emtak_kood           VARCHAR,
    tegevusala_emtak_tekstina       VARCHAR
);
"""

_TABLES = ("companies_detail", "contacts", "activities", "capitals", "annual_reports")

_INS_DETAIL   = "INSERT INTO companies_detail VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
_INS_CONTACT  = "INSERT INTO contacts VALUES (?,?,?,?,?,?)"
_INS_ACTIVITY = "INSERT INTO activities VALUES (?,?,?,?,?,?,?,?)"
_INS_CAPITAL  = "INSERT INTO capitals VALUES (?,?,?,?,?,?)"
_INS_REPORT   = "INSERT INTO annual_reports VALUES (?,?,?,?,?,?,?)"


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d.%m.%Y").date().isoformat()
    except Exception:
        return None


def safe_decimal(v):
    if v is None:
        return None
    try:
        return float(v) if not isinstance(v, str) else float(v.replace(",", "."))
    except Exception:
        return None


def safe_int(s):
    if s is None:
        return None
    try:
        return int(s)
    except Exception:
        return None


def import_json(zip_path=ZIP_PATH, db_path=DB_PATH):
    if not os.path.exists(zip_path):
        console.print(f"[red]ZIP not found:[/red] {zip_path}")
        return

    console.print(f"[bold]Database     :[/bold] {db_path}")
    console.print(f"[bold]Source       :[/bold] {zip_path}")
    console.print(f"[bold]ijson backend:[/bold] {_ijson_backend}")
    console.print()

    con = duckdb.connect(db_path)
    # Drop and recreate — faster than DELETE and avoids WAL recovery overhead
    # from any previously killed run.
    for tbl in _TABLES:
        con.execute(f"DROP TABLE IF EXISTS {tbl}")
    con.execute(DDL)

    b_detail, b_contact, b_activity, b_capital, b_report = [], [], [], [], []

    def _insert(label, sql, rows):
        if not rows:
            return
        t0 = time.time()
        print(f"  {label}: {len(rows):,} rows ... ", end="", flush=True)
        con.executemany(sql, rows)
        print(f"{time.time() - t0:.2f}s")
        rows.clear()

    def flush():
        con.begin()
        _insert("companies", _INS_DETAIL,   b_detail)
        _insert("contacts",  _INS_CONTACT,  b_contact)
        _insert("activities",_INS_ACTIVITY, b_activity)
        _insert("capitals",  _INS_CAPITAL,  b_capital)
        _insert("reports",   _INS_REPORT,   b_report)
        con.commit()

    start = time.time()
    count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold green]{task.description}"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        transient=False,
    ) as progress:
        task = progress.add_task("Importing records…", total=None)

        with zipfile.ZipFile(zip_path) as zf:
            with zf.open(JSON_NAME) as f:
                for rec in _ijson_items(f, "item"):
                    code = str(rec["ariregistri_kood"])
                    nimi = rec.get("nimi")
                    yl = rec.get("yldandmed", {})

                    # ── companies_detail ─────────────────────────────────
                    b_detail.append((
                        code,
                        nimi,
                        yl.get("ettevotteregistri_nr"),
                        parse_date(yl.get("esmaregistreerimise_kpv")),
                        parse_date(yl.get("kustutamise_kpv")),
                        yl.get("staatus"),
                        yl.get("staatus_tekstina"),
                        yl.get("piirkond"),
                        yl.get("piirkond_tekstina"),
                        yl.get("piirkond_tekstina_pikk"),
                        yl.get("oiguslik_vorm"),
                        yl.get("oiguslik_vorm_tekstina"),
                        yl.get("oigusliku_vormi_alaliik"),
                        yl.get("on_raamatupidamiskohustuslane"),
                        yl.get("tegutseb_tekstina"),
                        yl.get("esitab_kasusaajad"),
                    ))

                    # ── contacts ─────────────────────────────────────────
                    for c in yl.get("sidevahendid", []):
                        kid = c.get("kirje_id")
                        if kid is not None:
                            b_contact.append((
                                int(kid), code,
                                c.get("liik"),
                                c.get("liik_tekstina"),
                                c.get("sisu"),
                                parse_date(c.get("lopp_kpv")),
                            ))

                    # ── activities ───────────────────────────────────────
                    for a in yl.get("teatatud_tegevusalad", []):
                        kid = a.get("kirje_id")
                        if kid is not None:
                            b_activity.append((
                                int(kid), code,
                                a.get("emtak_kood"),
                                a.get("emtak_tekstina"),
                                a.get("nace_kood"),
                                a.get("on_pohitegevusala"),
                                parse_date(a.get("algus_kpv")),
                                parse_date(a.get("lopp_kpv")),
                            ))

                    # ── capitals ─────────────────────────────────────────
                    for k in yl.get("kapitalid", []):
                        kid = k.get("kirje_id")
                        if kid is not None:
                            b_capital.append((
                                int(kid), code,
                                safe_decimal(k.get("kapitali_suurus")),
                                k.get("kapitali_valuuta"),
                                parse_date(k.get("algus_kpv")),
                                parse_date(k.get("lopp_kpv")),
                            ))

                    # ── annual reports ───────────────────────────────────
                    for r in yl.get("info_majandusaasta_aruannetest", []):
                        kid = r.get("kirje_id")
                        if kid is not None:
                            b_report.append((
                                int(kid), code,
                                parse_date(r.get("majandusaasta_perioodi_algus_kpv")),
                                parse_date(r.get("majandusaasta_perioodi_lopp_kpv")),
                                safe_int(r.get("tootajate_arv")),
                                r.get("tegevusala_emtak_kood"),
                                r.get("tegevusala_emtak_tekstina"),
                            ))

                    count += 1
                    progress.advance(task)
                    if count % BATCH_SIZE == 0:
                        flush()

        flush()  # final partial batch

    elapsed = time.time() - start

    def rc(tbl):
        return con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]

    console.print()
    console.print("[bold green]✓ Import complete![/bold green]")
    console.print(f"  Companies        : [bold]{rc('companies_detail'):,}[/bold]")
    console.print(f"  Contacts         : [bold]{rc('contacts'):,}[/bold]")
    console.print(f"  Business acts    : [bold]{rc('activities'):,}[/bold]")
    console.print(f"  Capital records  : [bold]{rc('capitals'):,}[/bold]")
    console.print(f"  Annual reports   : [bold]{rc('annual_reports'):,}[/bold]")
    console.print(f"  Time elapsed     : [bold]{elapsed:.1f}s[/bold]")
    console.print(f"  Throughput       : [bold]{int(count / elapsed):,} records/sec[/bold]")

    con.close()


if __name__ == "__main__":
    import_json()
