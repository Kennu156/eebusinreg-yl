"""
db.py — DuckDB database layer.

Why DuckDB?
- Columnar storage → fast analytical queries (stats, county filters)
- Native CSV ingestion with COPY-style bulk loading (very fast)
- Zero-config, embedded — no server needed
- Proper DATE type support
- Handles NULLs and encoding natively
"""

import duckdb
import os
from pathlib import Path

DB_PATH = os.environ.get("REGISTRY_DB", "registry.duckdb")

DDL = """
CREATE TABLE IF NOT EXISTS companies (
    ariregistri_kood        VARCHAR PRIMARY KEY,
    nimi                    VARCHAR,
    ettevotja_oiguslik_vorm VARCHAR,
    ettevotja_oigusliku_vormi_alaliik VARCHAR,
    kmkr_nr                 VARCHAR,
    ettevotja_staatus       VARCHAR,
    ettevotja_staatus_tekstina VARCHAR,
    ettevotja_esmakande_kpv DATE,
    ettevotja_aadress       VARCHAR,
    asukoht_ettevotja_aadressis VARCHAR,
    asukoha_ehak_kood       VARCHAR,
    asukoha_ehak_tekstina   VARCHAR,
    indeks_ettevotja_aadressis VARCHAR,
    ads_adr_id              VARCHAR,
    ads_ads_oid             VARCHAR,
    ads_normaliseeritud_taisaadress VARCHAR,
    teabesysteemi_link      VARCHAR
);
"""


def get_connection(db_path: str = DB_PATH) -> duckdb.DuckDBPyConnection:
    """Open (or create) the DuckDB database."""
    return duckdb.connect(db_path)


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create table if it doesn't exist."""
    con.execute(DDL)


def import_csv(con: duckdb.DuckDBPyConnection, csv_path: str) -> int:
    """
    Bulk-import CSV into DuckDB using native CSV reader.
    Uses INSERT OR REPLACE (via staging table) for idempotency.

    Returns number of rows imported.
    """
    # Drop staging table if leftover from interrupted run
    con.execute("DROP TABLE IF EXISTS companies_staging")

    # DuckDB native CSV read — handles BOM, semicolon delimiter, NULLs, date parsing
    con.execute(f"""
        CREATE TABLE companies_staging AS
        SELECT
            NULLIF(TRIM(ariregistri_kood), '')     AS ariregistri_kood,
            NULLIF(TRIM(nimi), '')                 AS nimi,
            NULLIF(TRIM(ettevotja_oiguslik_vorm), '') AS ettevotja_oiguslik_vorm,
            NULLIF(TRIM(ettevotja_oigusliku_vormi_alaliik), '') AS ettevotja_oigusliku_vormi_alaliik,
            NULLIF(TRIM(kmkr_nr), '')              AS kmkr_nr,
            NULLIF(TRIM(ettevotja_staatus), '')    AS ettevotja_staatus,
            NULLIF(TRIM(ettevotja_staatus_tekstina), '') AS ettevotja_staatus_tekstina,
            TRY_CAST(
                CASE
                    WHEN TRIM(ettevotja_esmakande_kpv) = '' THEN NULL
                    ELSE strptime(TRIM(ettevotja_esmakande_kpv), '%d.%m.%Y')
                END AS DATE
            )                                      AS ettevotja_esmakande_kpv,
            NULLIF(TRIM(ettevotja_aadress), '')    AS ettevotja_aadress,
            NULLIF(TRIM(asukoht_ettevotja_aadressis), '') AS asukoht_ettevotja_aadressis,
            NULLIF(TRIM(asukoha_ehak_kood), '')    AS asukoha_ehak_kood,
            NULLIF(TRIM(asukoha_ehak_tekstina), '') AS asukoha_ehak_tekstina,
            NULLIF(TRIM(indeks_ettevotja_aadressis), '') AS indeks_ettevotja_aadressis,
            NULLIF(TRIM(ads_adr_id), '')           AS ads_adr_id,
            NULLIF(TRIM(ads_ads_oid), '')          AS ads_ads_oid,
            NULLIF(TRIM(ads_normaliseeritud_taisaadress), '') AS ads_normaliseeritud_taisaadress,
            NULLIF(TRIM(teabesysteemi_link), '')   AS teabesysteemi_link
        FROM read_csv(
            '{csv_path}',
            delim=';',
            header=true,
            quote='"',
            encoding='utf-8',
            skip=0,
            ignore_errors=true,
            all_varchar=true
        )
        WHERE TRIM(ariregistri_kood) != ''
          AND ariregistri_kood IS NOT NULL
    """)

    row_count = con.execute("SELECT COUNT(*) FROM companies_staging").fetchone()[0]

    # Upsert: insert or replace using staging table
    con.execute("""
        INSERT OR REPLACE INTO companies
        SELECT * FROM companies_staging
    """)

    con.execute("DROP TABLE companies_staging")
    return row_count


def get_stats(con: duckdb.DuckDBPyConnection) -> dict:
    """Return summary statistics about the database."""
    total = con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    by_status = con.execute("""
        SELECT ettevotja_staatus_tekstina, COUNT(*) as cnt
        FROM companies
        GROUP BY ettevotja_staatus_tekstina
        ORDER BY cnt DESC
    """).fetchall()
    by_form = con.execute("""
        SELECT ettevotja_oiguslik_vorm, COUNT(*) as cnt
        FROM companies
        WHERE ettevotja_oiguslik_vorm IS NOT NULL
        GROUP BY ettevotja_oiguslik_vorm
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()
    earliest = con.execute("""
        SELECT MIN(ettevotja_esmakande_kpv) FROM companies
        WHERE ettevotja_esmakande_kpv IS NOT NULL
    """).fetchone()[0]
    return {
        "total": total,
        "by_status": by_status,
        "by_form": by_form,
        "earliest_registration": earliest,
    }


def search_companies(
    con: duckdb.DuckDBPyConnection,
    name: str = None,
    status: str = None,
    county: str = None,
    limit: int = 50,
) -> list[dict]:
    """Search companies with optional filters."""
    conditions = []
    params = []

    if name:
        conditions.append("nimi ILIKE ?")
        params.append(f"%{name}%")
    if status:
        conditions.append("ettevotja_staatus_tekstina ILIKE ?")
        params.append(f"%{status}%")
    if county:
        conditions.append("asukoha_ehak_tekstina ILIKE ?")
        params.append(f"%{county}%")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"""
        SELECT ariregistri_kood, nimi, ettevotja_oiguslik_vorm,
               ettevotja_staatus_tekstina, ettevotja_esmakande_kpv,
               asukoha_ehak_tekstina, teabesysteemi_link
        FROM companies
        {where}
        ORDER BY nimi
        LIMIT {limit}
    """
    rows = con.execute(query, params).fetchall()
    cols = ["code", "name", "legal_form", "status", "registered", "county", "link"]
    return [dict(zip(cols, row)) for row in rows]


def get_company(con: duckdb.DuckDBPyConnection, code: str) -> dict | None:
    """Look up a single company by registry code."""
    row = con.execute(
        "SELECT * FROM companies WHERE ariregistri_kood = ?", [code]
    ).fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in con.description]
    return dict(zip(cols, row))


def export_csv(
    con: duckdb.DuckDBPyConnection,
    output_path: str,
    name: str = None,
    status: str = None,
    county: str = None,
) -> int:
    """Export filtered results to a CSV file."""
    conditions = []
    params = []

    if name:
        conditions.append("nimi ILIKE ?")
        params.append(f"%{name}%")
    if status:
        conditions.append("ettevotja_staatus_tekstina ILIKE ?")
        params.append(f"%{status}%")
    if county:
        conditions.append("asukoha_ehak_tekstina ILIKE ?")
        params.append(f"%{county}%")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Use DuckDB's native COPY TO for fast export
    param_clause = ""
    if params:
        # Materialise filtered set to temp table for COPY
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE export_tmp AS
            SELECT * FROM companies {where}
        """, params)
        con.execute(f"COPY export_tmp TO '{output_path}' (HEADER, DELIMITER ',')")
        count = con.execute("SELECT COUNT(*) FROM export_tmp").fetchone()[0]
        con.execute("DROP TABLE export_tmp")
    else:
        con.execute(f"COPY companies TO '{output_path}' (HEADER, DELIMITER ',')")
        count = con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]

    return count