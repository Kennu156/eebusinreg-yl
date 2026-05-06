"""
downloader.py — Download and extract the Estonian Business Registry ZIP.

Design decisions:
- Streams the download in chunks (no full file in memory at once)
- Extracts CSV in-memory from the ZIP (avoids writing the ZIP to disk)
- Writes extracted CSV to a temp file for DuckDB to read via read_csv()
- Handles network errors, incomplete downloads, and Ctrl+C gracefully
"""

import io
import os
import signal
import sys
import tempfile
import zipfile
from contextlib import contextmanager

import requests
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

DOWNLOAD_URL = (
    "https://avaandmed.ariregister.rik.ee/sites/default/files/avaandmed/"
    "ettevotja_rekvisiidid__lihtandmed.csv.zip"
)
CHUNK_SIZE = 1024 * 256  # 256 KB chunks
CONNECT_TIMEOUT = 15     # seconds
READ_TIMEOUT = 120       # seconds


class DownloadError(Exception):
    pass


@contextmanager
def _interrupt_guard(tmp_path: str):
    """Remove temp file if user hits Ctrl+C mid-download."""
    original = signal.getsignal(signal.SIGINT)

    def handler(sig, frame):
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        print("\n[!] Download interrupted. Temporary files cleaned up.")
        sys.exit(1)

    signal.signal(signal.SIGINT, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, original)


def download_and_extract(url: str = DOWNLOAD_URL) -> str:
    """
    Download the ZIP from `url`, extract the CSV, write it to a temp file.
    Returns the path to the extracted CSV temp file.
    The caller is responsible for deleting the temp file.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv", prefix="registry_")
    os.close(tmp_fd)

    with _interrupt_guard(tmp_path):
        try:
            response = requests.get(
                url,
                stream=True,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                headers={"User-Agent": "EstonianRegistryImporter/1.0"},
            )
            response.raise_for_status()
        except requests.exceptions.ConnectionError as e:
            os.unlink(tmp_path)
            raise DownloadError(f"Connection failed: {e}") from e
        except requests.exceptions.Timeout as e:
            os.unlink(tmp_path)
            raise DownloadError(f"Request timed out: {e}") from e
        except requests.exceptions.HTTPError as e:
            os.unlink(tmp_path)
            raise DownloadError(f"HTTP error {response.status_code}: {e}") from e

        total = int(response.headers.get("content-length", 0)) or None

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            transient=True,
        ) as progress:
            dl_task = progress.add_task("Downloading registry ZIP…", total=total)

            zip_buffer = io.BytesIO()
            downloaded = 0

            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                zip_buffer.write(chunk)
                downloaded += len(chunk)
                progress.update(dl_task, advance=len(chunk))

        # Verify we got something
        if downloaded == 0:
            os.unlink(tmp_path)
            raise DownloadError("Downloaded 0 bytes — server returned empty response.")

        # Extract CSV from ZIP
        zip_buffer.seek(0)
        try:
            with zipfile.ZipFile(zip_buffer) as zf:
                csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if not csv_names:
                    os.unlink(tmp_path)
                    raise DownloadError("No CSV file found inside the ZIP archive.")

                csv_name = csv_names[0]

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[bold green]{task.description}"),
                    BarColumn(),
                    TimeElapsedColumn(),
                    transient=True,
                ) as progress:
                    extract_task = progress.add_task(
                        f"Extracting {csv_name}…", total=None
                    )
                    with zf.open(csv_name) as src, open(tmp_path, "wb") as dst:
                        while True:
                            data = src.read(CHUNK_SIZE)
                            if not data:
                                break
                            dst.write(data)
                            progress.update(extract_task, advance=len(data))

        except zipfile.BadZipFile as e:
            os.unlink(tmp_path)
            raise DownloadError(
                f"Downloaded file is not a valid ZIP (may be truncated): {e}"
            ) from e

    return tmp_path
