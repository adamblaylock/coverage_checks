#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path("data/output")
DEFAULT_COVERAGE_ROOT = Path("data/coverage")
DEFAULT_DOWNLOAD_DIR = Path("data/downloads")
DEFAULT_CATALOG_DIR = Path("data/catalog")


@dataclass
class BatchResult:
    input_path: Path
    output_path: Path
    success: bool
    duration_seconds: float
    error: str | None = None


def run(*args):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run([str(x) for x in args], check=True)


def release_safe(release_id: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in release_id)


def read_selected_release(catalog_dir: Path = DEFAULT_CATALOG_DIR) -> str:
    return (catalog_dir / "selected_release.txt").read_text().strip()


def discover_input_files(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise SystemExit(f"Input directory not found: {input_dir}")
    files = [path for path in input_dir.glob("*.csv") if path.is_file()]
    if not files:
        raise SystemExit(f"No CSV files found in {input_dir}")
    return sorted(files, key=lambda path: (-path.stat().st_size, path.name))


def extract_state_code(input_path: Path) -> str:
    """Extract the state code from the input filename.

    Expects a filename ending in _XX.csv where XX is a two-letter state code
    (e.g. coverage_check_national_20260831_NJ.csv -> NJ).
    Falls back to the full stem if no match is found.
    """
    match = re.search(r"_([A-Z]{2})$", input_path.stem, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return input_path.stem


def output_path_for(input_path: Path, output_dir: Path) -> Path:
    state = extract_state_code(input_path)
    today = date.today().strftime("%Y%m%d")
    return output_dir / f"coverage_check_{state}_{today}.csv"


def print_file_summary(files: list[Path]) -> None:
    print(f"Found {len(files)} input file(s):")
    for path in files:
        print(f"  - {path.name}: {path.stat().st_size:,} bytes")


def purge_directory(path: Path) -> None:
    path = path.expanduser()
    if path.exists() and not path.is_dir():
        raise RuntimeError(f"Expected directory path for purge: {path}")
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    print(f"Purged {path}")


def empty_macos_trash() -> None:
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Finder" to empty trash'],
            check=True,
            capture_output=True,
        )
        print("Emptied macOS Trash")
    except Exception as exc:
        print(f"Warning: could not empty macOS Trash: {exc}")


def cleanup_artifacts(coverage_root: Path, download_dir: Path) -> None:
    purge_directory(coverage_root)
    purge_directory(download_dir)
    empty_macos_trash()


def run_pipeline_for_file(
    py: Path,
    input_path: Path,
    output_path: Path,
    *,
    providers: str,
    as_of: str | None,
    force_download: bool,
    coverage_root: Path,
    download_dir: Path,
    catalog_dir: Path = DEFAULT_CATALOG_DIR,
) -> str:
    run(py, "init_database.py")
    sync = [
        py,
        "sync_fcc_data.py",
        "--input",
        input_path,
        "--providers",
        providers,
        "--download-dir",
        download_dir,
        "--coverage-root",
        coverage_root,
    ]
    if as_of:
        sync += ["--as-of", as_of]
    if force_download:
        sync += ["--force"]
    run(*sync)
    release = read_selected_release(catalog_dir)
    coverage_dir = coverage_root / release_safe(release)
    run(
        py,
        "load_postgis.py",
        "--coverage-dir",
        coverage_dir,
        "--input",
        input_path,
        "--release-id",
        release,
        "--replace-states",
        "--subdivide",
    )
    run(
        py,
        "process_addresses.py",
        "--input",
        input_path,
        "--output",
        output_path,
        "--release-id",
        release,
    )
    return release


def run_batch(args: argparse.Namespace, files: list[Path]) -> list[BatchResult]:
    results: list[BatchResult] = []
    py = Path(sys.executable)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not args.skip_docker:
        run("docker", "compose", "up", "-d")
    for index, input_path in enumerate(files, start=1):
        output_path = output_path_for(input_path, args.output_dir)
        print(
            f"\n=== [{index}/{len(files)}] {input_path.name} "
            f"({input_path.stat().st_size:,} bytes) ==="
        )
        file_started = time.monotonic()
        error: str | None = None
        success = False
        try:
            release = run_pipeline_for_file(
                py,
                input_path,
                output_path,
                providers=args.providers,
                as_of=args.as_of,
                force_download=args.force_download,
                coverage_root=args.coverage_root,
                download_dir=args.download_dir,
            )
            print(f"Complete: {output_path} (release {release})")
            success = True
        except Exception as exc:
            error = str(exc)
            print(f"ERROR: {input_path.name}: {exc}")
        finally:
            cleanup_artifacts(args.coverage_root, args.download_dir)
        results.append(
            BatchResult(
                input_path=input_path,
                output_path=output_path,
                success=success,
                duration_seconds=time.monotonic() - file_started,
                error=error,
            )
        )
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the coverage pipeline for every CSV file in a folder."
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--providers", default="att,tmo,vzw")
    parser.add_argument("--as-of")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--coverage-root", type=Path, default=DEFAULT_COVERAGE_ROOT)
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    files = discover_input_files(args.input_dir)
    print_file_summary(files)
    started = time.monotonic()
    results = run_batch(args, files)
    failures = [result for result in results if not result.success]
    print("\nBatch summary:")
    for result in results:
        status = "SUCCESS" if result.success else "FAILED"
        suffix = f" -> {result.output_path}"
        if result.error:
            suffix += f" ({result.error})"
        print(f"  - {result.input_path.name}: {status}{suffix}")
    print(f"Duration: {time.monotonic() - started:.1f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
