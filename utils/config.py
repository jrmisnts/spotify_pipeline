"""Loader dan validasi config/tables.yml, dipakai bersama oleh extract dan load."""
import itertools
import re
from pathlib import Path

import yaml

def _find_root():
    """Root project = folder terdekat di atas file ini yang berisi config/tables.yml.
    Jadi tidak bergantung pada kedalaman folder (tes/utils/ atau tes/script/utils/)."""
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "config" / "tables.yml").is_file():
            return p
    return here.parents[1]

ROOT = _find_root()
DEFAULT_CONFIG = ROOT / "config" / "table_top_items.yaml"

# Key yang ditulis extract ke setiap file landing (envelope JSON).
# Kolom bronze diisi dari key ini, dari key `iterate`, atau dari `source_file` (diisi loader).
ENVELOPE_KEYS = {"extract_date", "run_id", "item_type", "fetched_at", "payload"}
SAFE_TYPE = re.compile(r"^[a-z][a-z0-9_ ]*(\(\d+(,\s*\d+)?\))?$")
ALLOWED_DEFAULTS = {"now()", "current_date"}


class ConfigError(Exception):
    pass


def validate_bronze(name, b):
    if not isinstance(b, dict):
        raise ConfigError(f"{name}: blok 'bronze' tidak ada")

    landing = b.get("landing") or {}
    requests = (b.get("extract") or {}).get("requests")
    load = b.get("load") or {}

    # --- landing
    if not landing.get("path"):
        raise ConfigError(f"{name}.bronze.landing.path wajib diisi")
    if "://" in str(landing["path"]):
        raise ConfigError(
            f"{name}.bronze.landing.path '{landing['path']}': storage cloud belum didukung, gunakan path lokal"
        )
    if landing.get("format", "json") != "json":
        raise ConfigError(f"{name}.bronze.landing.format: hanya 'json' yang didukung")

    # --- load
    for key in ("table", "primary_keys", "columns"):
        if not load.get(key):
            raise ConfigError(f"{name}.bronze.load.{key} wajib diisi")
    if "." not in load["table"]:
        raise ConfigError(f"{name}.bronze.load.table harus berformat schema.nama, bukan '{load['table']}'")

    for c in load["columns"]:
        if not isinstance(c, dict) or "name" not in c or "type" not in c:
            raise ConfigError(f"{name}: setiap kolom wajib punya name dan type, bukan {c!r}")
        if not SAFE_TYPE.match(str(c["type"]).lower()):
            raise ConfigError(f"{name}: tipe kolom '{c['type']}' ({c['name']}) tidak valid")
        if "default" in c and c["default"] not in ALLOWED_DEFAULTS:
            raise ConfigError(f"{name}: default '{c['default']}' tidak diizinkan {sorted(ALLOWED_DEFAULTS)}")

    col_types = {c["name"]: str(c["type"]).lower() for c in load["columns"]}
    unknown_pk = set(load["primary_keys"]) - set(col_types)
    if unknown_pk:
        raise ConfigError(f"{name}: primary_keys tidak ada di columns: {sorted(unknown_pk)}")
    if "source_file" not in load["primary_keys"]:
        raise ConfigError(f"{name}: primary_keys harus memuat 'source_file' (dasar idempotensi load)")
    if col_types.get("payload") != "jsonb":
        raise ConfigError(f"{name}: kolom 'payload' bertipe jsonb wajib ada (respons API mentah)")

    # --- extract
    if not requests:
        raise ConfigError(f"{name}.bronze.extract.requests wajib diisi")
    for r in requests:
        if not str(r.get("method", "")).startswith("current_user_"):
            raise ConfigError(f"{name}: method '{r.get('method')}' harus diawali 'current_user_'")
        if not r.get("item_type"):
            raise ConfigError(f"{name}: setiap request wajib punya item_type")
        supplied = ENVELOPE_KEYS | set((r.get("iterate") or {}).keys()) | {"source_file"}
        missing = [c["name"] for c in load["columns"] if "default" not in c and c["name"] not in supplied]
        if missing:
            raise ConfigError(
                f"{name}/{r['item_type']}: kolom {missing} tidak punya sumber nilai "
                f"(bukan key envelope, bukan key iterate, dan tanpa default)"
            )


def load_config(path=DEFAULT_CONFIG):
    """Return {nama_tabel: blok bronze} setelah divalidasi."""
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    tables = cfg.get("tables")
    if not tables:
        raise ConfigError(f"{path}: key 'tables' kosong atau tidak ada")
    for name, t in tables.items():
        validate_bronze(name, (t or {}).get("bronze"))
    return {name: t["bronze"] for name, t in tables.items()}


def landing_dir(b):
    p = Path(b["landing"]["path"])
    return p if p.is_absolute() else ROOT / p


def expand_iterations(request):
    """Satu dict parameter per kombinasi `iterate`; tanpa iterate menghasilkan satu dict kosong."""
    iterate = request.get("iterate") or {}
    keys = list(iterate)
    for combo in itertools.product(*(iterate[k] for k in keys)):
        yield dict(zip(keys, combo))
