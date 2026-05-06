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
"""

import os
import time
import zipfile
import ijson
import duckdb
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, MofNCompleteColumn

console = Console()
DB_PATH = os.environ.get("REGISTRY_DB", "registry.duckdb")
ZIP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ettevotja_rekvisiidid__yldandmed.json.zip")
JSON_NAME = "ettevotja_rekvisiidid__yldandmed.json"

BATCH_SIZE = 500  # rows per insert batch

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


def parse_date(s):
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(s, "%d.%m.%Y").date().isoformat()
    except Exception:
        return None


def safe_decimal(s):
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "."))
    except Exception:
        return None


def safe_int(s):
    if s is None:
        return None
    try:
        return int(s)
    except Exception:
        return None


def executemany_safe(con, sql, rows):
    if rows:
        con.executemany(sql, rows)


def import_json(zip_path=ZIP_PATH, db_path=DB_PATH):
    if not os.path.exists(zip_path):
        console.print(f"[red]ZIP not found:[/red] {zip_path}")
        return

    console.print(f"[bold]Database:[/bold] {db_path}")
    console.print(f"[bold]Source:[/bold] {zip_path}")
    console.print()

    con = duckdb.connect(db_path)
    con.execute(DDL)

    # Drop existing data for idempotency
    for tbl in ("companies_detail", "contacts", "activities", "capitals", "annual_reports"):
        con.execute(f"DELETE FROM {tbl}")

    # Prepared INSERT statements
    ins_detail = """
        INSERT OR REPLACE INTO companies_detail VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    ins_contact = "INSERT OR REPLACE INTO contacts VALUES (?,?,?,?,?,?)"
    ins_activity = "INSERT OR REPLACE INTO activities VALUES (?,?,?,?,?,?,?,?)"
    ins_capital = "INSERT OR REPLACE INTO capitals VALUES (?,?,?,?,?,?)"
    ins_report = "INSERT OR REPLACE INTO annual_reports VALUES (?,?,?,?,?,?,?)"

    # Batches
    b_detail, b_contact, b_activity, b_capital, b_report = [], [], [], [], []

    def flush():
        executemany_safe(con, ins_detail, b_detail)
        executemany_safe(con, ins_contact, b_contact)
        executemany_safe(con, ins_activity, b_activity)
        executemany_safe(con, ins_capital, b_capital)
        executemany_safe(con, ins_report, b_report)
        b_detail.clear(); b_contact.clear(); b_activity.clear()
        b_capital.clear(); b_report.clear()

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
                for rec in ijson.items(f, "item"):
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
                        if kid:
                            b_contact.append((
                                kid, code,
                                c.get("liik"),
                                c.get("liik_tekstina"),
                                c.get("sisu"),
                                parse_date(c.get("lopp_kpv")),
                            ))

                    # ── activities ───────────────────────────────────────
                    for a in yl.get("teatatud_tegevusalad", []):
                        kid = a.get("kirje_id")
                        if kid:
                            b_activity.append((
                                kid, code,
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
                        if kid:
                            b_capital.append((
                                kid, code,
                                safe_decimal(k.get("kapitali_suurus")),
                                k.get("kapitali_valuuta"),
                                parse_date(k.get("algus_kpv")),
                                parse_date(k.get("lopp_kpv")),
                            ))

                    # ── annual reports ───────────────────────────────────
                    for r in yl.get("info_majandusaasta_aruannetest", []):
                        kid = r.get("kirje_id")
                        if kid:
                            b_report.append((
                                kid, code,
                                parse_date(r.get("majandusaasta_perioodi_algus_kpv")),
                                parse_date(r.get("majandusaasta_perioodi_lopp_kpv")),
                                safe_int(r.get("tootajate_arv")),
                                r.get("tegevusala_emtak_kood"),
                                r.get("tegevusala_emtak_tekstina"),
                            ))

                    count += 1
                    if count % BATCH_SIZE == 0:
                        flush()
                        progress.update(task, completed=count, description=f"Importing records… {count:,}")

        flush()  # final batch

    elapsed = time.time() - start

    # Row counts
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
    console.print(f"  Throughput       : [bold]{int(count/elapsed):,} records/sec[/bold]")

    con.close()


if __name__ == "__main__":
    import_json()