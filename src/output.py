"""
output.py — Render search/get results as table, JSON, or CSV.
"""

import csv
import io
import json
import sys
from datetime import date
from typing import Any


def _json_default(obj):
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def format_results(rows: list[dict], fmt: str = "table") -> None:
    """Print rows in the requested format."""
    if not rows:
        print("No results found.")
        return

    if fmt == "json":
        print(json.dumps(rows, default=_json_default, ensure_ascii=False, indent=2))
    elif fmt == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    else:
        _print_table(rows)


def _print_table(rows: list[dict]) -> None:
    """Rich-powered table output."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(show_header=True, header_style="bold cyan", show_lines=False)

    cols = list(rows[0].keys())
    col_labels = {
        "code": "Code",
        "name": "Name",
        "legal_form": "Legal Form",
        "status": "Status",
        "registered": "Registered",
        "county": "County",
        "link": "Link",
    }

    for col in cols:
        label = col_labels.get(col, col)
        justify = "left"
        no_wrap = col in ("code", "registered")
        table.add_column(label, justify=justify, no_wrap=no_wrap)

    for row in rows:
        table.add_row(*[str(v) if v is not None else "" for v in row.values()])

    console.print(table)


def format_company_detail(company: dict) -> None:
    """Pretty-print a single company's full details."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    label_map = {
        "ariregistri_kood": "Registry Code",
        "nimi": "Name",
        "ettevotja_oiguslik_vorm": "Legal Form",
        "ettevotja_oigusliku_vormi_alaliik": "Legal Form Subtype",
        "kmkr_nr": "VAT Number",
        "ettevotja_staatus": "Status Code",
        "ettevotja_staatus_tekstina": "Status",
        "ettevotja_esmakande_kpv": "First Registered",
        "ettevotja_aadress": "Address",
        "asukoht_ettevotja_aadressis": "Location in Address",
        "asukoha_ehak_kood": "EHAK Code",
        "asukoha_ehak_tekstina": "EHAK Location",
        "indeks_ettevotja_aadressis": "Postal Code",
        "ads_adr_id": "ADS Address ID",
        "ads_ads_oid": "ADS Object ID",
        "ads_normaliseeritud_taisaadress": "Normalised Address",
        "teabesysteemi_link": "Registry Link",
    }

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value")

    for key, label in label_map.items():
        val = company.get(key)
        display = str(val) if val is not None else "[dim]—[/dim]"
        table.add_row(label, display)

    name = company.get("nimi") or "Company Detail"
    console.print(Panel(table, title=f"[bold]{name}[/bold]", border_style="cyan"))