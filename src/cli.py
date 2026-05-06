"""
cli.py — Click-based CLI for the Estonian Business Registry importer.

Commands:
  import   Download + import all records
  stats    Show database statistics
  search   Search by name / status / county
  get      Look up a single company by code
  export   Export filtered results to CSV
"""

import os
import sys
import time

import click
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from src import db as database
from src.output import format_company_detail, format_results

console = Console()

DB_PATH_OPTION = click.option(
    "--db",
    default=os.environ.get("REGISTRY_DB", "registry.duckdb"),
    show_default=True,
    help="Path to the DuckDB database file.",
    envvar="REGISTRY_DB",
)

FORMAT_OPTION = click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json", "csv"]),
    show_default=True,
    help="Output format.",
)


@click.group()
@click.version_option("1.0.0", prog_name="importer")
def cli():
    """Estonian Business Registry importer — download, query, and export company data."""
    pass


# ─── import ─────────────────────────────────────────────────────────────────

@cli.command("import")
@DB_PATH_OPTION
@click.option(
    "--url",
    default=None,
    help="Override the download URL (useful for testing with a local file).",
)
@click.option(
    "--local",
    "local_file",
    default=None,
    type=click.Path(exists=True),
    help="Import from a local CSV file instead of downloading.",
)
def import_cmd(db, url, local_file):
    """Download and import all registry records into the database."""
    start = time.time()

    # ── Acquire CSV ──────────────────────────────────────────────────────────
    if local_file:
        csv_path = local_file
        tmp_to_delete = None
        console.print(f"[bold green]Using local file:[/bold green] {local_file}")
    else:
        from src.downloader import DownloadError, download_and_extract
        download_url = url or None
        console.print("[bold]Step 1/3:[/bold] Downloading registry data…")
        try:
            if download_url:
                csv_path = download_and_extract(download_url)
            else:
                csv_path = download_and_extract()
            tmp_to_delete = csv_path
        except DownloadError as e:
            console.print(f"[bold red]Download failed:[/bold red] {e}")
            sys.exit(1)

    console.print(f"[bold]Step 2/3:[/bold] Setting up database at [cyan]{db}[/cyan]…")

    # ── Import ───────────────────────────────────────────────────────────────
    con = database.get_connection(db)
    database.ensure_schema(con)

    console.print("[bold]Step 3/3:[/bold] Importing records…")

    import_start = time.time()
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold green]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
    ) as progress:
        progress.add_task("Inserting rows into DuckDB…", total=None)
        row_count = database.import_csv(con, csv_path)

    import_elapsed = time.time() - import_start
    total_elapsed = time.time() - start
    rps = int(row_count / import_elapsed) if import_elapsed > 0 else 0

    # ── Cleanup temp file ────────────────────────────────────────────────────
    if tmp_to_delete and os.path.exists(tmp_to_delete):
        os.unlink(tmp_to_delete)

    con.close()

    console.print()
    console.print("[bold green]✓ Import complete![/bold green]")
    console.print(f"  Records imported : [bold]{row_count:,}[/bold]")
    console.print(f"  Import time      : [bold]{import_elapsed:.2f}s[/bold]")
    console.print(f"  Total time       : [bold]{total_elapsed:.2f}s[/bold]")
    console.print(f"  Throughput       : [bold]{rps:,} records/sec[/bold]")
    console.print(f"  Database         : [cyan]{db}[/cyan]")


# ─── stats ───────────────────────────────────────────────────────────────────

@cli.command()
@DB_PATH_OPTION
def stats(db):
    """Show statistics about the imported data."""
    _require_db(db)
    con = database.get_connection(db)
    database.ensure_schema(con)
    s = database.get_stats(con)
    con.close()

    console.print(f"\n[bold cyan]Database:[/bold cyan] {db}")
    console.print(f"[bold]Total companies:[/bold] {s['total']:,}")

    if s["earliest_registration"]:
        console.print(f"[bold]Earliest registration:[/bold] {s['earliest_registration']}")

    console.print("\n[bold]By status:[/bold]")
    for status_text, count in s["by_status"]:
        bar = "█" * min(30, int(30 * count / s["total"]))
        label = status_text or "(unknown)"
        console.print(f"  {label:<30} {count:>8,}  {bar}")

    console.print("\n[bold]Top legal forms:[/bold]")
    for form, count in s["by_form"]:
        console.print(f"  {form:<35} {count:>8,}")


# ─── search ──────────────────────────────────────────────────────────────────

@cli.command()
@DB_PATH_OPTION
@FORMAT_OPTION
@click.option("--name", default=None, help="Partial name match (case-insensitive).")
@click.option("--status", default=None, help="Status text filter (e.g. 'Registrisse kantud').")
@click.option("--county", default=None, help="County / region filter (e.g. 'Harju maakond').")
@click.option("--limit", default=50, show_default=True, help="Maximum number of results.")
def search(db, fmt, name, status, county, limit):
    """Search companies by name, status, and/or county."""
    _require_db(db)

    if not any([name, status, county]):
        console.print("[yellow]Tip:[/yellow] Specify at least one filter: --name, --status, --county")
        sys.exit(1)

    con = database.get_connection(db)
    database.ensure_schema(con)
    results = database.search_companies(con, name=name, status=status, county=county, limit=limit)
    con.close()

    if fmt == "table":
        console.print(f"\nFound [bold]{len(results)}[/bold] result(s):\n")
    format_results(results, fmt)


# ─── get ─────────────────────────────────────────────────────────────────────

@cli.command()
@DB_PATH_OPTION
@FORMAT_OPTION
@click.option("--code", required=True, help="Registry code (ariregistri_kood).")
def get(db, fmt, code):
    """Look up a single company by registry code."""
    _require_db(db)

    con = database.get_connection(db)
    database.ensure_schema(con)
    company = database.get_company(con, code)
    con.close()

    if not company:
        console.print(f"[bold red]Not found:[/bold red] No company with code [cyan]{code}[/cyan]")
        sys.exit(1)

    if fmt == "json":
        import json
        from src.output import _json_default
        print(json.dumps(company, default=_json_default, ensure_ascii=False, indent=2))
    elif fmt == "csv":
        import csv
        writer = csv.DictWriter(sys.stdout, fieldnames=company.keys())
        writer.writeheader()
        writer.writerow(company)
    else:
        format_company_detail(company)


# ─── export ──────────────────────────────────────────────────────────────────

@cli.command()
@DB_PATH_OPTION
@click.option("--name", default=None, help="Filter by name (partial match).")
@click.option("--status", default=None, help="Filter by status text.")
@click.option("--county", default=None, help="Filter by county.")
@click.option(
    "--output",
    "-o",
    required=True,
    help="Output CSV file path.",
    type=click.Path(),
)
def export(db, name, status, county, output):
    """Export filtered results to a CSV file."""
    _require_db(db)

    con = database.get_connection(db)
    database.ensure_schema(con)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True) as p:
        p.add_task("Exporting…", total=None)
        count = database.export_csv(con, output, name=name, status=status, county=county)

    con.close()
    console.print(f"[bold green]✓[/bold green] Exported [bold]{count:,}[/bold] records to [cyan]{output}[/cyan]")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _require_db(db_path: str) -> None:
    """Exit with a helpful message if the database doesn't exist yet."""
    if not os.path.exists(db_path):
        console.print(
            f"[bold red]Database not found:[/bold red] [cyan]{db_path}[/cyan]\n"
            "Run [bold]./importer.py import[/bold] first to download and import the data."
        )
        sys.exit(1)