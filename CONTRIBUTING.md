# Contributing to LynxAct Coach

LynxAct Coach is a Flask tactical-analysis demo (hackathon origin, Apache-2.0).
The repo is small — here's the short version for contributors.

## Dev setup

```bash
git clone https://github.com/Lxcardoza993/lynxact-coach.git
cd lynxact-coach
pip install -r requirements-dev.txt   # runtime deps + pytest + ruff
cp .env.example .env                  # replay mode by default, zero keys needed
```

## Run the app

```bash
python3 app.py   # http://127.0.0.1:6901
```

## Tests, lint, security

```bash
python3 -m pytest                                              # test suite
python3 -m pytest --cov=coach --cov=app --cov-report=term-missing  # + coverage
ruff check coach app.py                                        # lint
python3 -m bandit -r coach app.py                              # security scan
pip-audit -r requirements.txt                                 # dependency CVE scan (pinned runtime deps)
```

`pyproject.toml` sets `pythonpath = ["."]`, so run pytest from the repo
root — no `PYTHONPATH` env var is needed. Coverage sits near 99%; keep it
there for any change you make.

## Code style

- **Type annotations** on all function signatures (PEP 484; `dict | None`,
  `list[dict]`, `tuple[...]` modern style — Python 3.10+).
- **Docstrings** on public functions (PEP 257).
- ruff rule set is `E/F/W/I/UP/B/S` (see `pyproject.toml`); `app.py` and
  `tests/` carry targeted per-file ignores (the `load_dotenv()`-before-imports
  ordering in `app.py`; `assert` as the test primitive in `tests/`).

## Pull requests

Small, focused PRs. Before requesting review:

- all tests pass, coverage stays ~99%,
- `ruff check` and `bandit` are clean (mark intentional safe subprocess with
  `# nosec` + an inline reason, as the existing `ffmpeg`/`ffprobe` calls do).

## Reporting security issues

Email **founder@lxlynx.com** with details. Please do not open a public issue
for security vulnerabilities — give us a chance to patch first.

## Rollback guidance

### If `feat/telestration` causes issues after merge

**Option 1: Revert commit (non-linear history)**

```bash
git checkout main
git revert <commit-hash>              # revert the telestration merge commit
git push origin main
```

**Option 2: Branch back to previous state (linear history)**

```bash
# Before merging feat/telestration, the main branch pointed to a9a6a3c
git checkout -b rollback-telestration
git reset --hard a9a6a3c
git push origin rollback-telestration
```

Then open a PR from `rollback-telestration` → `main`.

**Rollback checklist**

- [ ] Stop any production servers running the app
- [ ] Run `git revert` or `git reset --hard`
- [ ] Verify tests pass: `pytest --cov=coach --cov-report=term-missing`
- [ ] Verify lints: `ruff check .`
- [ ] Verify security: `bandit -r coach app.py`
- [ ] Push and test on staging before production

## Telestration feature quick reference

| Feature | Description |
|---------|-------------|
| Keyboard | `Space`: play/pause while drawing; `Esc`: exit drawing mode |
| Tools | Arrow (➤), Freehand (✎), Rect (▭) |
| Colors | Gold (#f2cc60), Blue (#58a6ff), Green (#3fb950), Red (#f85149) |
| Point limit | 2–600 points per stroke, 200 strokes per clip |
| Storage | `data/annotations/<clip_id>.json`, atomic writes |
| Export | `frame PNG` button: captures frame + visible strokes as PNG |
