"""Cross-platform task runner — same targets as the Makefile, for machines without `make`.

Usage: python tasks.py <target> [extra args passed to the underlying command]
       python tasks.py eval --hidden
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def venv_python() -> str:
    """Prefer the project venv's interpreter so `python tasks.py` works from any shell."""
    for cand in (VENV / "Scripts" / "python.exe", VENV / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return sys.executable


def run(cmd: list[str], cwd: Path = ROOT) -> int:
    print("$", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=cwd)


def module_cmd(module: str, milestone: str, extra: list[str]) -> int:
    py = venv_python()
    # Check the module exists without importing it in this (possibly non-venv) interpreter.
    probe = subprocess.call(
        [py, "-c", f"import importlib.util as u,sys;sys.exit(0 if u.find_spec('{module}') else 1)"],
        cwd=ROOT,
    )
    if probe != 0:
        print(f"'{module}' is not built yet (arrives in milestone {milestone}).")
        return 0
    return run([py, "-m", module, *extra])


def t_setup(extra: list[str]) -> int:
    if not VENV.exists():
        rc = run([sys.executable, "-m", "venv", str(VENV)])
        if rc:
            return rc
    py = venv_python()
    rc = run([py, "-m", "pip", "install", "--upgrade", "pip"]) or run(
        [py, "-m", "pip", "install", "-e", ".[dev]"]
    )
    if rc:
        return rc
    if (ROOT / "web" / "package.json").exists() and shutil.which("npm"):
        rc = run([shutil.which("npm") or "npm", "install"], cwd=ROOT / "web")
    return rc


def t_test(extra: list[str]) -> int:
    return run([venv_python(), "-m", "pytest", *extra])


def t_lint(extra: list[str]) -> int:
    return run([venv_python(), "-m", "ruff", "check", "freshroute", "api", "tests", *extra])


def t_schemas(extra: list[str]) -> int:
    return run([venv_python(), "-m", "freshroute.schemas", "--export", *extra])


def t_fetch(extra: list[str]) -> int:
    """Download real data: Agmarknet slice (~75 min stream), OSRM distances, festival calendar."""
    for module in ("freshroute.fetch_osrm", "freshroute.calendar_features",
                   "freshroute.fetch_agmarknet"):
        rc = run([venv_python(), "-m", module, *extra])
        if rc:
            return rc
    return 0


def t_fit(extra: list[str]) -> int:
    return module_cmd("freshroute.fit_market", "M2", extra)


def t_gen(extra: list[str]) -> int:
    return module_cmd("freshroute.generator", "M1", extra)


def t_eval(extra: list[str]) -> int:
    return module_cmd("freshroute.eval_runner", "M1", extra)


def t_api(extra: list[str]) -> int:
    if not (ROOT / "api" / "main.py").exists():
        print("API is not built yet (arrives in milestone M5).")
        return 0
    uvicorn = [venv_python(), "-m", "uvicorn", "api.main:app", "--reload", "--port", "8000"]
    return run([*uvicorn, *extra])


def t_web(extra: list[str]) -> int:
    if not (ROOT / "web" / "package.json").exists():
        print("Frontend is not built yet (arrives in milestone M6).")
        return 0
    return run([shutil.which("npm") or "npm", "run", "dev", *extra], cwd=ROOT / "web")


TARGETS = {
    "setup": t_setup,
    "fetch": t_fetch,
    "fit": t_fit,
    "gen": t_gen,
    "test": t_test,
    "eval": t_eval,
    "api": t_api,
    "web": t_web,
    "lint": t_lint,
    "schemas": t_schemas,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in TARGETS:
        print(__doc__)
        print("targets:", ", ".join(TARGETS))
        return 2
    os.environ.setdefault("PYTHONUTF8", "1")  # ₹ and ° in console output on Windows
    return TARGETS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
