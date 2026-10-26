"""Tahap 1: Spotify Web API -> file JSON mentah di landing (folder lokal).

Setiap respons API ditulis sebagai satu file (envelope: metadata + payload mentah):
    <landing.path>/extract_date=YYYY-MM-DD/run_id=<uuid>/<item_type>[__<iterate>=<nilai>].json

Contoh:
    python ingestion/extract_to_landing.py                    # semua tabel di config
    python ingestion/extract_to_landing.py --table top_items  # satu tabel saja
    python ingestion/extract_to_landing.py --dry-run          # lihat rencana, tanpa API dan tanpa tulis file
"""
import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone

import spotipy
import yaml
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

# Pastikan folder ingestion/ ada di sys.path, apa pun cara script dijalankan
# (python path/ke/script.py, python -m, tombol Run di VS Code, dari folder mana saja).
PROJECT_ROOT = r"c:\Users\jerem\tes"      # folder yang berisi utils/, config/, script/
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.config import DEFAULT_CONFIG, ROOT, ConfigError, expand_iterations, landing_dir, load_config  # noqa: E402

load_dotenv(ROOT / ".env")

SCOPE = "user-read-recently-played user-top-read"


def landing_filename(request, it):
    parts = [request["item_type"]] + [f"{k}={re.sub(r'[^A-Za-z0-9_.-]', '_', str(v))}" for k, v in it.items()]
    return "__".join(parts) + ".json"


def write_atomic(path, obj):
    """Tulis ke file sementara lalu rename, supaya loader tidak pernah membaca file setengah jadi."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def fetch(sp, request, iter_params):
    method = getattr(sp, request["method"])
    return method(**{**(request.get("params") or {}), **iter_params})


def extract_table(sp, name, b, run_id, extract_date):
    """Return (jumlah file ditulis, jumlah request gagal)."""
    base = landing_dir(b) / f"extract_date={extract_date}" / f"run_id={run_id}"
    written = failed = 0
    for request in b["extract"]["requests"]:
        for it in expand_iterations(request):
            label = f"{name}/{request['item_type']}" + (f" {it}" if it else "")
            try:
                payload = fetch(sp, request, it)
            except spotipy.SpotifyException as e:
                # Endpoint bisa dibatasi Spotify (Development Mode); lanjut ke request lain.
                failed += 1
                print(f"[WARN] {label}: HTTP {e.http_status} - {e.msg}", file=sys.stderr)
                continue
            envelope = {
                "extract_date": str(extract_date),
                "run_id": run_id,
                "item_type": request["item_type"],
                **it,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "_request": {"method": request["method"], "params": {**(request.get("params") or {}), **it}},
                "payload": payload,
            }
            path = base / landing_filename(request, it)
            write_atomic(path, envelope)
            written += 1
            print(f"  {label}: {len(payload.get('items', []))} item -> {path.relative_to(landing_dir(b))}")
    return written, failed


def get_spotify():
    # Membaca SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI dari environment.
    # Pertama kali, browser terbuka untuk login; token disimpan di file .cache.
    return spotipy.Spotify(auth_manager=SpotifyOAuth(scope=SCOPE))


def print_plan(tables, selected):
    for name in selected:
        b = tables[name]
        print(f"{name} -> {landing_dir(b)}")
        for r in b["extract"]["requests"]:
            for it in expand_iterations(r):
                params = {**(r.get("params") or {}), **it}
                args_str = ", ".join(f"{k}={v!r}" for k, v in params.items())
                print(f"  {r['method']}({args_str})  -> {landing_filename(r, it)}")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="path ke tables.yml")
    p.add_argument("--table", action="append", help="nama tabel di config (boleh diulang); default semua")
    p.add_argument("--dry-run", action="store_true", help="validasi config dan tampilkan rencana, tanpa API/tulis file")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        tables = load_config(args.config)
    except (ConfigError, OSError, yaml.YAMLError) as e:
        sys.exit(f"Config error: {e}")

    selected = args.table or list(tables)
    unknown = [t for t in selected if t not in tables]
    if unknown:
        sys.exit(f"Tabel tidak ada di config: {unknown}. Tersedia: {list(tables)}")

    if args.dry_run:
        print_plan(tables, selected)
        return

    run_id = uuid.uuid4().hex
    extract_date = datetime.now(timezone.utc).date()  # UTC, konsisten dengan fetched_at
    print(f"run_id={run_id} extract_date={extract_date}")

    sp = get_spotify()
    total_written = total_failed = 0
    for name in selected:
        w, f = extract_table(sp, name, tables[name], run_id, extract_date)
        total_written += w
        total_failed += f
    print(f"selesai: {total_written} file ditulis, {total_failed} request gagal")
    if total_failed:
        sys.exit("Ada request yang gagal (lihat [WARN] di atas).")


if __name__ == "__main__":
    main()