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
