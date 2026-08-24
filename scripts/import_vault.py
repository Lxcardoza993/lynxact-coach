"""Import the local technique-vault library into data/vault for the demo
picker, generating a preview poster per clip.

One-shot op-doc tool: dedupes the vault's per-file duplicates (the library
keeps two copies of every clip), skips the three clips that already served
as demos and were retired, copies the rest into data/vault (gitignored),
probes each with ffprobe and renders a 480px poster with ffmpeg.

Usage:  python3 scripts/import_vault.py [source_dir]

Writes data/import_manifest.md with the import record (id, size, duration,
license) and exits non-zero only on hard failure; per-clip probe errors are
recorded in the manifest rather than aborting the run.
"""
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT = os.path.join(BASE, "data", "vault")
POSTERS = os.path.join(BASE, "data", "posters")
MANIFEST = os.path.join(BASE, "data", "import_manifest.md")
DEFAULT_SRC = (
    "/home/li/football-dribbling-vault/sports/football/videos"
)

# Clips that already served as demos and were retired at the user's request —
# not re-imported so the list starts clean and the user re-picks from scratch.
RETIRED = {
    "body-feint_diego-maradona_1986.mp4",
    "elastico_ronaldinho_2006.mp4",
    "la-croqueta_lionel-messi_2015.mp4",
}


def run(cmd: list[str], timeout: int = 60) -> str | None:
    try:
        out = subprocess.run(  # nosec
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def ffprobe_duration(path: str) -> float:
    ffprobe = os.environ.get("FFPROBE", "ffprobe")
    raw = run([ffprobe, "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", path])
    try:
        return float(raw or 0)
    except ValueError:
        return 0.0


def make_poster(src: str, dest: str, duration: float) -> bool:
    """One frame around a third of the way in, 480px wide."""
    ffmpeg = os.environ.get("FFMPEG", "ffmpeg")
    t = min(max(duration / 3.0, 0.3), max(duration - 0.2, 0.3))
    return run([ffmpeg, "-y", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1",
                "-vf", "scale=480:-2", "-q:v", "3", dest]) is not None


def find_sources(src_dir: str) -> dict[str, str]:
    """basename -> absolute path, deduping the library's duplicate copies."""
    found: dict[str, str] = {}
    for root, _, files in os.walk(src_dir):
        for fname in sorted(files):
            if fname.lower().endswith(".mp4") and fname not in RETIRED:
                found.setdefault(fname, os.path.join(root, fname))
    return found


def main() -> int:
    src_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    if not os.path.isdir(src_dir):
        print(f"source dir not found: {src_dir}", file=sys.stderr)
        return 2
    sources = find_sources(src_dir)
    if not sources:
        print("no mp4 files found to import", file=sys.stderr)
        return 2
    os.makedirs(VAULT, exist_ok=True)
    os.makedirs(POSTERS, exist_ok=True)
    rows, failures = [], []
    for fname, path in sorted(sources.items()):
        clip_id = fname[:-4]
        dest = os.path.join(VAULT, fname)
        shutil.copy2(path, dest)
        duration = ffprobe_duration(dest)
        size = os.path.getsize(dest)
        poster_ok = make_poster(dest, os.path.join(POSTERS, clip_id + ".jpg"),
                                duration)
        if duration <= 0:
            failures.append((fname, "ffprobe failed"))
        if not poster_ok:
            failures.append((fname, "poster failed"))
        rows.append((clip_id, f"{size / 1024 / 1024:.1f}M", f"{duration:.1f}s",
                     "✓" if poster_ok and duration > 0 else "⚠"))
    md = ["# Vault import manifest (2026-08-24)\n",
          "| clip_id | size | duration | ok |",
          "|---|---|---|---|"]
    md += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
    md += ["", f"Imported {len(rows)} clips from `{src_dir}` into `data/vault`.",
           "License: 自有素材(球员动作切片,可自由演示)。",
           f"Retired demos skipped: {', '.join(sorted(RETIRED))}.",
           f"Failures: {failures or 'none'}"]
    with open(MANIFEST, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"imported {len(rows)} clips -> {VAULT}")
    print(f"posters -> {POSTERS}; manifest -> {MANIFEST}")
    if failures:
        print(f"{len(failures)} warning(s): {failures}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
