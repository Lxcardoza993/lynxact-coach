#!/usr/bin/env python3
"""One-time migration of the local vault (videos + posters) to Google Drive,
then write the cloud catalog.json via the Drive API (coach.storage).

Idempotent: `rclone copy` skips files already on Drive, and the catalog is
rebuilt from what the API actually sees in the cloud folders — safe to rerun.

Usage:  python3 scripts/sync_to_gdrive.py [--no-upload]
  --no-upload  skip rclone transfers (e.g. files already up, only rewrite catalog)
"""
import argparse
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()  # pull DRIVE_REFRESH_TOKEN (and overrides) from .env

from coach import storage  # noqa: E402
from coach.clips import parse_stem  # noqa: E402

VAULT_DIR = os.path.join(BASE, "data", "vault")
POSTER_DIR = os.path.join(BASE, "data", "posters")
REMOTE = "gdrive:lynxact-coach"


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    out = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603 — fixed argv
    if check and out.returncode != 0:
        print(f"!! {' '.join(cmd)} -> rc={out.returncode}\n{out.stderr.strip()}")
        sys.exit(1)
    return out


def ffprobe_duration(path: str) -> float:
    ffprobe = os.environ.get("FFPROBE", "ffprobe")
    out = run([ffprobe, "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", path])
    try:
        return float((out.stdout or "").strip() or 0)
    except ValueError:
        print(f"!! no duration for {path}")
        return 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-upload", action="store_true",
                        help="skip rclone transfers, only (re)write the catalog")
    parser.add_argument("--write-env", action="store_true",
                        help="pin the resolved Drive ids into .env (deterministic startup, "
                             "value-safe: key=value lines written without printing)")
    args = parser.parse_args()

    if not storage.enabled():
        print("!! DRIVE_REFRESH_TOKEN not set — cannot sync. Aborting.")
        sys.exit(1)

    rclone = os.environ.get("RCLONE", "rclone")
    local_vault = sorted(f for f in os.listdir(VAULT_DIR) if f.lower().endswith(".mp4"))
    local_posters = sorted(f for f in os.listdir(POSTER_DIR) if f.lower().endswith(".jpg"))
    print(f"local: {len(local_vault)} mp4, {len(local_posters)} posters")

    if not args.no_upload:
        # rclone copy creates the remote folders implicitly; also touch
        # 'uploads' so the app's upload folder exists before first upload.
        for sub in ("vault", "posters", "uploads"):
            run([rclone, "mkdir", f"{REMOTE}/{sub}"])
        print("uploading videos…")
        run([rclone, "copy", VAULT_DIR, f"{REMOTE}/vault",
             "--transfers=4", "--checkers=8", "--retries=3"], check=True)
        print("uploading posters…")
        run([rclone, "copy", POSTER_DIR, f"{REMOTE}/posters",
             "--transfers=4", "--checkers=8", "--retries=3"], check=True)
    else:
        print("--no-upload: skipping rclone transfers")

    # --- Drive API phase: resolve ids, build the catalog from cloud truth ---
    print("resolving Drive folders…")
    vault_id = storage.folder_id(storage.VAULT_FOLDER)
    poster_id = storage.folder_id(storage.POSTER_FOLDER)
    if not vault_id or not poster_id:
        print("!! vault/posters folders not found on Drive — run without --no-upload first")
        sys.exit(1)

    cloud_names = {f["name"]: f for f in storage.list_folder(vault_id) if f["name"].lower().endswith(".mp4")}
    poster_names = {f["name"] for f in storage.list_folder(poster_id)}
    missing = [n for n in cloud_names if n[:-4] + ".jpg" not in poster_names]
    missing_vid = [n for n in local_vault if n not in cloud_names]

    entries = []
    for name in sorted(cloud_names):
        clip_id = name[:-4]
        technique, player, year = parse_stem(clip_id)
        local_path = os.path.join(VAULT_DIR, name)
        duration = ffprobe_duration(local_path) if os.path.exists(local_path) else 0.0
        entries.append({
            "id": clip_id,
            "title": f"{player.title()} — {technique.title()} ({year})",
            "duration": round(duration, 2),
            "file_id": f"{cloud_names[name]['id']}",
        })

    def replace_vault(cat: dict) -> None:
        cat["vault"] = entries          # keep 'uploads' exactly as it is

    storage.update_catalog(replace_vault)
    print(f"catalog written: {len(entries)} vault entries")

    # --- verify from the cloud side ---
    got = storage.get_catalog()
    if len(got.get("vault", [])) != len(entries):
        print("!! catalog readback mismatch — aborting before anyone serves stale data")
        sys.exit(1)
    lsl = run([rclone, "lsl", REMOTE, "--max-depth", "2"]).stdout.strip().splitlines()
    print(f"verify: Drive has {len(lsl)} objects under {REMOTE} (expect "
          f"{len(cloud_names)} mp4 + {len(poster_names)} jpg + catalog.json + folders)")
    if missing_vid:
        print(f"!! local videos missing on Drive: {missing_vid} — rerun without --no-upload")
        sys.exit(1)
    if missing:
        print(f"note: {len(missing)} videos have no poster on Drive: {missing[:5]}…")

    if args.write_env:
        pins = {
            "DRIVE_ROOT_FOLDER_ID": storage.root_id(),
            "DRIVE_VAULT_FOLDER_ID": vault_id,
            "DRIVE_POSTER_FOLDER_ID": poster_id,
            "DRIVE_UPLOAD_FOLDER_ID": storage.folder_id(storage.UPLOAD_FOLDER),
            "DRIVE_CATALOG_FILE_ID": storage.catalog_id(),
        }
        if not all(pins.values()):
            print("!! cannot pin env ids — some folders unresolved; aborting")
            sys.exit(1)
        env_path = os.path.join(BASE, ".env")
        with open(env_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for key, value in pins.items():
            lines = [ln for ln in lines if not ln.startswith(key + "=")]
            lines.append(f"{key}={value}")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("env pinned with 5 DRIVE id keys (values not printed)")


if __name__ == "__main__":
    main()
