#!/usr/bin/env python3
"""
🪔 VIDYAJYOTI — ONE-CLICK RUNNER  (no external AI APIs — fully self-hosted)

Run this single file and it will:
  STEP 0  Install missing Python modules (psutil, psycopg2-binary) automatically.
  STEP 1  Check/install Node.js + npm (Windows: winget/choco/download; apt; brew),
          then npm install the frontend deps.
  STEP 2  Create .venv per service and pip-install its requirements.txt.
  STEP 3  Install & start PostgreSQL if missing (apt/brew/MSI/winget/docker),
          create role/db/schema idempotently, pre-create tables.
  STEP 4  Launch backend :8000, frontend :3000, ML service :9000 as background
          processes with logs in .vj_logs/. Ctrl+C stops everything cleanly.

Usage:
    python runner.py                # run everything
    python runner.py --setup        # install/check only, don't start servers
    python runner.py --no-ml        # skip ML service
    python runner.py --no-install   # skip system installs (node/postgres)
    python runner.py --backend-only | --frontend-only | --ml-only
"""

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
BACKEND_DIR = ROOT / "backend"
ML_DIR = ROOT / "backend_ml"
LOG_DIR = ROOT / ".vj_logs"

BACKEND_PORT = 8000
FRONTEND_PORT = 3000
ML_PORT = 9000
PG_PORT = 5432

PG_USER, PG_PASS, PG_DB = "vj_user", "vj_password", "vidyajyoti"

PROCESSES: list[subprocess.Popen] = []
ACTUAL_FRONTEND_PORT = 3000   # set by start_frontend() if :3000 was occupied


# ------------------------------------------------------------------ helpers
def banner(title: str):
    print("\n" + "=" * 70)
    print(" " + title)
    print("=" * 70)


def info(msg):  print(f"[INFO] {msg}")
def ok(msg):    print(f"[ OK ] {msg}")
def warn(msg):  print(f"[WARN] {msg}")
def fail(msg):  print(f"[FAIL] {msg}")


def run(cmd, cwd=None, check=False, shell=False, capture=False, env=None):
    """Run a command, printing it. Robust on Windows for .cmd shims."""
    display = cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)
    info(f"$ {display}")
    kwargs = dict(cwd=str(cwd) if cwd else None, env=env)
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    p = subprocess.run(cmd, shell=shell, **kwargs)
    if capture:
        out = p.stdout or ""
        print(out[-2000:])
    if check and p.returncode != 0:
        raise RuntimeError(f"Command failed ({p.returncode}): {display}")
    return p


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def wait_for_port(port: int, timeout: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if port_open(port):
            return True
        time.sleep(0.5)
    return False


def http_ok(url: str, timeout: float = 2.0) -> bool:
    """GET a URL and return True only on a real HTTP 2xx/3xx/4xx response."""
    try:
        from urllib.request import urlopen
        with urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def wait_for_http(url: str, timeout: float = 90.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if http_ok(url):
            return True
        time.sleep(0.5)
    return False


def kill_port(port: int):
    """Free a port from stale processes before launching."""
    try:
        if IS_WINDOWS:
            out = subprocess.check_output(["netstat", "-ano", "-p", "TCP"], text=True, errors="ignore")
            pids = set()
            for line in out.splitlines():
                cols = line.split()
                if len(cols) >= 5 and f":{port}" in cols[1] and cols[3].upper() == "LISTENING":
                    pids.add(cols[4])
            for pid in pids:
                if pid != "0" and int(pid) != os.getpid():
                    info(f"Killing stale process on port {port}: PID {pid}")
                    subprocess.call(["taskkill", "/F", "/PID", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                import psutil
                for c in psutil.net_connections(kind="inet"):
                    if c.laddr and c.laddr.port == port and c.status == "LISTEN" and c.pid:
                        info(f"Killing stale process on port {port}: PID {c.pid}")
                        psutil.Process(c.pid).terminate()
            except ImportError:
                pass
    except Exception:
        pass


def venv_python(base: Path) -> Path:
    return base / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


# ------------------------------------------------------------------ STEP 0
def ensure_module(import_name: str, pip_name: str | None = None):
    pip_name = pip_name or import_name
    try:
        __import__(import_name)
        return True
    except ImportError:
        warn(f"Python module '{import_name}' not found — installing via pip ...")
        rc = subprocess.call([sys.executable, "-m", "pip", "install", "--quiet", pip_name])
        if rc == 0:
            ok(f"Module '{import_name}' installed successfully.")
            return True
        fail(f"Could not install '{pip_name}'.")
        return False


def syntax_preflight():
    """Fast (stdlib-only) compile check of every .py in the project. A single
    broken file used to make ml_service hang at 'Waiting for application
    startup' with no visible cause; now we print it before launching."""
    bad = []
    for base in (BACKEND_DIR, ML_DIR):
        for p in sorted(base.rglob("*.py")):
            if ".venv" in p.parts:
                continue
            try:
                compile(p.read_text(encoding="utf-8", errors="ignore"),
                        str(p), "exec")
            except SyntaxError as e:
                bad.append((p, f"line {e.lineno}: {e.msg}"))
    if bad:
        warn("Python syntax errors found — services WILL fail until fixed:")
        for p, msg in bad:
            print(f"   ✗ {p.relative_to(ROOT)}\n     {msg}")
    else:
        ok("All backend/ML Python files compile cleanly.")


def step0_python_modules():
    banner("STEP 0 — Checking required Python modules")
    ensure_module("psutil")
    ensure_module("psycopg2", "psycopg2-binary")
    syntax_preflight()


# ------------------------------------------------------------------ STEP 1
def find_npm():
    """Return (npm_cmd, node_exe) using absolute paths — immune to quoting bugs."""
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    node = shutil.which("node") or shutil.which("node.exe")
    candidates = [
        r"C:\Program Files\nodejs\npm.cmd", r"C:\Program Files\nodejs\node.exe",
        os.path.expandvars(r"%APPDATA%\npm\npm.cmd"),
        "/usr/local/bin/npm", "/usr/bin/npm", "/opt/homebrew/bin/npm",
    ]
    npm = npm or next((c for c in candidates if Path(c).exists()), None)
    node = node or next((c for c in candidates if Path(c).exists()), None)
    return npm, node


def install_node():
    warn("Node.js/npm not found — attempting automatic installation ...")
    if IS_WINDOWS:
        if shutil.which("winget"):
            rc = run(["winget", "install", "-e", "--accept-package-agreements",
                      "--accept-source-agreements", "OpenJS.NodeJS.LTS"]).returncode
            if rc == 0:
                _refresh_path(); return find_npm()[0] is not None
        if shutil.which("choco"):
            run(["choco", "install", "nodejs-lts", "-y"])
            _refresh_path(); return find_npm()[0] is not None
        # Last resort: download official MSI and install silently.
        try:
            import urllib.request
            msi = ROOT / "node-lts.msi"
            url = "https://nodejs.org/dist/v22.14.0/node-v22.14.0-x64.msi"
            info(f"Downloading {url} ...")
            urllib.request.urlretrieve(url, msi)
            run(["msiexec", "/i", str(msi), "/qn"], shell=False)
            _refresh_path(); return find_npm()[0] is not None
        except Exception as e:
            warn(f"MSI install failed: {e}")
    elif IS_LINUX:
        if shutil.which("apt"):
            sudo = ["sudo"] if os.getuid() != 0 else []
            run(sudo + ["apt-get", "update", "-y"])
            run(sudo + ["apt-get", "install", "-y", "nodejs", "npm"])
            return find_npm()[0] is not None
        if shutil.which("dnf"):
            run(["sudo", "dnf", "install", "-y", "nodejs", "npm"]); return find_npm()[0] is not None
    elif IS_MAC:
        if shutil.which("brew"):
            run(["brew", "install", "node"]); return find_npm()[0] is not None
    warn("Automatic Node.js install failed. Install from https://nodejs.org and re-run.")
    return False


def _refresh_path():
    """Pick up PATH changes made by installers in this session."""
    os.environ["PATH"] = os.environ.get("PATH", "")
    for p in [r"C:\Program Files\nodejs", os.path.expandvars(r"%APPDATA%\npm")]:
        if Path(p).exists() and p not in os.environ["PATH"]:
            os.environ["PATH"] += os.pathsep + p


def _frontend_deps_healthy(nm: Path) -> bool:
    """True only if node_modules actually contains what Vite needs to RUN.

    A previous interrupted/corrupted npm install left trees that EXIST but are
    incomplete (e.g. rollup's platform binary package missing → vite crashes
    with MODULE_NOT_FOUND at startup, which looked like 'blank white screen').
    We therefore check for the critical files, not just the folder."""
    checks = [
        nm / "vite" / "bin" / "vite.js",
        nm / "react" / "index.js",
        nm / "react-dom" / "client.js",
        nm / "@vitejs" / "plugin-react" / "package.json",
        nm / "tailwindcss" / "package.json",
        nm / "@tailwindcss" / "vite" / "package.json",
        nm / "framer-motion" / "package.json",
        nm / "lucide-react" / "package.json",
        nm / "sonner" / "package.json",
    ]
    if not all(p.exists() for p in checks):
        return False
    # Rollup loads a PLATFORM-SPECIFIC native package at require-time
    # (rollup/dist/native.js). If npm skipped the optional dep (Windows,
    # interrupted install, --no-optional), vite dies instantly. Verify one
    # matching the current platform exists.
    try:
        rm = nm / "rollup" / "dist" / "native.js"
        if rm.exists():
            plat = {"nt": "win32", "posix": sys.platform.replace("darwin", "darwin")}.get(os.name, "linux")
            variants = list((nm / "@rollup").glob(f"rollup-{plat}*"))
            if not variants:
                return False
    except Exception:
        return False
    return True


def step1_node(do_install: bool):
    banner("STEP 1 — Node.js & frontend dependencies")
    npm, node = find_npm()
    if not npm:
        if do_install and install_node():
            npm, node = find_npm()
    if not npm:
        fail("Node.js/npm unavailable — frontend cannot be built/started.")
        return False
    ok(f"Node.js/npm found: {npm}")
    lock = FRONTEND_DIR / "package-lock.json"
    nm = FRONTEND_DIR / "node_modules"
    healthy = nm.exists() and lock.exists() and _frontend_deps_healthy(nm)
    if not healthy:
        if nm.exists():
            info("node_modules present but INCOMPLETE/CORRUPTED (missing vite/"
                 "react/rollup-binary files) — reinstalling cleanly ...")
            shutil.rmtree(nm, ignore_errors=True)
        info("Installing frontend dependencies (npm install) ...")
        rc = run([npm, "install"], cwd=FRONTEND_DIR, shell=IS_WINDOWS).returncode
        if rc != 0:
            fail("npm install failed.")
            return False
        if not _frontend_deps_healthy(nm):
            fail("npm install finished but dependencies are still incomplete. "
                 "Delete frontend/node_modules and frontend/package-lock.json, "
                 "then re-run: python runner.py")
            return False
        ok("Frontend dependencies installed.")
    else:
        ok("Frontend dependencies installed and verified complete.")
    return True


# ------------------------------------------------------------------ STEP 2
ML_CORE_DEPS = ["fastapi>=0.115.0", "uvicorn[standard]>=0.32.0",
                "pydantic>=2.10.0", "numpy>=1.26", "scikit-learn>=1.4"]


def step2_service_deps(name: str, base: Path):
    """Install service deps into its .venv.

    The ML service is split in two tiers so it can ALWAYS start quickly:
      core  (fast, ~30 MB): fastapi/uvicorn/numpy/scikit-learn  -> installed now
      heavy (optional, GBs): torch/transformers/sentence-transformers/faiss
                            -> installed in a BACKGROUND thread; until then the
                               service runs with local heuristic/extractive models.
    """
    banner(f"STEP 2 — Installing '{name}' Python dependencies")
    req = base / "requirements.txt"
    if not req.exists():
        warn(f"No requirements.txt for {name} — skipping.")
        return
    py = venv_python(base)
    if not py.exists():
        info(f"Creating virtual environment at {base / '.venv'} ...")
        run([sys.executable, "-m", "venv", str(base / ".venv")])

    if name == "ml_service":
        core_stamp = base / ".venv" / ".core_installed"
        if not core_stamp.exists():
            info("pip installing CORE ML deps (fast — service starts immediately) ...")
            rc = run([str(py), "-m", "pip", "install", "--quiet", *ML_CORE_DEPS]).returncode
            if rc != 0:
                fail("pip install failed for 'ml_service' core deps. Re-run runner.py.")
                sys.exit(1)
            core_stamp.touch()
            ok("Core ML deps installed.")
        else:
            ok("'ml_service' core dependencies already installed.")
        heavy_stamp = base / ".venv" / ".heavy_installed"
        skipped = base / ".venv" / ".heavy_skipped"
        if heavy_stamp.exists():
            ok("'ml_service' heavy ML deps already installed.")
        elif skipped.exists():
            warn("Heavy ML deps were SKIPPED earlier (low disk space). Service "
                 "runs fine on self-hosted pure-Python models. Free up ~5 GB, "
                 "delete backend_ml/.venv/.heavy_skipped, re-run to upgrade.")
        else:
            info("Heavy ML deps (torch/transformers/faiss) will install in the "
                 "BACKGROUND — the service is fully functional meanwhile.")
            t = threading.Thread(target=_install_heavy_ml, args=(py, base, req),
                                 daemon=True)
            t.start()
        return

    stamp = base / ".venv" / ".deps_installed"
    if stamp.exists() and stamp.stat().st_mtime >= req.stat().st_mtime:
        ok(f"'{name}' dependencies already installed.")
        return
    info(f"pip installing {req.name} into .venv (be patient — first time may download a lot) ...")
    # Prebuilt wheels only — avoids pg_config / source-build failures on Windows.
    rc = run([str(py), "-m", "pip", "install", "--only-binary=:all:",
              "--upgrade", "pip"], check=False).returncode
    rc = run([str(py), "-m", "pip", "install", "-r", str(req)]).returncode
    if rc != 0:
        fail(f"pip install failed for '{name}'. Re-run runner.py after fixing.")
        sys.exit(1)
    stamp.touch()
    ok(f"'{name}' dependencies installed.")


def _disk_free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(str(path)).free / (1024 ** 3)
    except Exception:
        return 999.0


def _install_heavy_ml(py: Path, base: Path, req: Path):
    """Background installer for OPTIONAL heavy ML packages (self-hosted only).

    Guards learned from three incidents that used to leave ml_service looking
    broken forever ("always STARTING, never RUNNING"):
      * NEVER install inside the server process — this runs in a runner thread
        while uvicorn is already UP and serving /api/ml/health.
      * skip entirely if < 5 GB free disk (torch+transformers need ~3-4 GB);
      * --no-cache-dir so pip doesn't double the footprint;
      * Python-version-aware matrix: brand-new interpreters (e.g. 3.14) have
        NO wheels for torch/faiss/sentence-transformers — plain `pip install
        torch` fails with "No matching distribution found". We probe wheel
        availability first and print an actionable hint instead of looping on
        guaranteed-to-fail downloads;
      * ALWAYS write a clear result line into .vj_logs/ml_heavy_install.log.
    The service is fully functional WITHOUT these packages (deterministic
    pure-Python self-hosted models); they only upgrade it to transformer
    embeddings/generation later.
    """
    LOG_DIR.mkdir(exist_ok=True)
    log = LOG_DIR / "ml_heavy_install.log"
    with open(log, "a", encoding="utf-8") as fh:
        def w(msg):
            fh.write(msg + "\n"); fh.flush()

        w(f"\n=== background heavy-ML install started {time.ctime()} ===")
        free = _disk_free_gb(base)
        if free < 5.0:
            w(f"[SKIP] Only {free:.1f} GB free disk — heavy ML packages need "
              "~5 GB. Service keeps running on self-hosted pure-Python models.")
            w("[HINT] Free up disk space, then delete backend_ml/.venv/"
              ".heavy_skipped and re-run runner.py.")
            (base / ".venv" / ".heavy_skipped").touch()
            return

        # --- Python-version-aware package matrix -------------------------------
        ver = subprocess.run([str(py), "-c",
                              "import sys;print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True).stdout.strip() or "?"
        try:
            major, minor = (int(x) for x in ver.split("."))
        except ValueError:
            major, minor = 3, 12
        if (major, minor) >= (3, 14):
            w(f"[SKIP] Python {ver} has no published wheels yet for "
              "torch/faiss-cpu/sentence-transformers — installing would fail "
              "with 'No matching distribution found'.")
            w("[HINT] The ML service is FULLY FUNCTIONAL right now on its "
              "self-hosted pure-Python models. To enable the transformer "
              "stack later, recreate the venv with Python 3.11–3.13:")
            w('       py -3.12 -m venv backend_ml\\.venv && python runner.py')
            (base / ".venv" / ".heavy_skipped").touch()
            return

        heavy = ["faiss-cpu>=1.8", "sentence-transformers>=3.0",
                 "torch>=2.2", "transformers>=4.44"]
        w(f"[INFO] Python {ver}, {free:.1f} GB free — installing "
          "torch/transformers/faiss (several minutes; service stays UP) ...")
        cmd = [str(py), "-m", "pip", "install", "--quiet", "--no-cache-dir", *heavy]
        rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT)
        if rc != 0:
            w("[RETRY] first attempt failed — one automatic retry ...")
            time.sleep(5)
            rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT)
        if rc == 0:
            (base / ".venv" / ".heavy_installed").touch()
            w("=== heavy ML deps installed OK — restart runner.py to enable "
              "the transformer stack (health endpoint will show torch:true) ===")
        else:
            w("=== heavy ML install FAILED (service still runs on local "
              "self-hosted fallback models — NOT an app-breaking error) ===")


# ------------------------------------------------------------------ STEP 3
def try_psql_connect(user, dbname, password=None):
    """Try connecting with psycopg2 (md5 or trust). Returns conn or None."""
    try:
        import psycopg2
        for auth in ([{"password": password}, {}] if password else [{}]):
            try:
                conn = psycopg2.connect(host="localhost", port=PG_PORT, user=user,
                                         dbname=dbname, connect_timeout=2, **auth)
                conn.autocommit = True
                return conn
            except Exception:
                continue
    except ImportError:
        pass
    return None


def install_postgres():
    warn(f"No PostgreSQL server reachable at localhost:{PG_PORT} — auto-installing now ...")
    if IS_LINUX and shutil.which("apt"):
        sudo = ["sudo"] if os.getuid() != 0 else []
        if run(sudo + ["apt-get", "install", "-y", "postgresql", "postgresql-contrib"]).returncode == 0:
            run(sudo + ["service", "postgresql", "start"], check=False)
            return True
    if IS_MAC and shutil.which("brew"):
        if run(["brew", "install", "postgresql@16"]).returncode == 0:
            run(["brew", "services", "start", "postgresql@16"], check=False)
            return True
    if IS_WINDOWS:
        if shutil.which("winget"):
            if run(["winget", "install", "-e", "--accept-package-agreements",
                    "--accept-source-agreements", "PostgreSQL.16"]).returncode == 0:
                _refresh_path()
                return wait_for_port(PG_PORT, 60)
        if shutil.which("choco"):
            run(["choco", "install", "postgresql", "-y"], check=False)
            return wait_for_port(PG_PORT, 60)
    if shutil.which("docker"):
        info("Trying Docker container for PostgreSQL ...")
        run(["docker", "rm", "-f", "vj-pg"], check=False)
        if run(["docker", "run", "-d", "--name", "vj-pg", "-p", "5432:5432",
                "-e", "POSTGRES_PASSWORD=postgres", "postgres:16"]).returncode == 0:
            return wait_for_port(PG_PORT, 90)
    return False


def bootstrap_database():
    """Create role/db/grants idempotently using an admin connection."""
    admin = None
    for user in ("postgres", "administrator", os.environ.get("USER", ""), "root"):
        if not user:
            continue
        admin = try_psql_connect(user, "postgres", password=os.environ.get("PGPASSWORD"))
        if admin:
            break
    if admin is None:
        warn("Connected to PostgreSQL but no admin credentials worked."
             "\n  Run this SQL once as superuser, then re-run runner.py:"
             f"\n    CREATE ROLE {PG_USER} LOGIN PASSWORD '{PG_PASS}';"
             f"\n    CREATE DATABASE {PG_DB} OWNER {PG_USER};"
             f"\n    \\c {PG_DB}"
             f"\n    GRANT ALL ON SCHEMA public TO {PG_USER};")
        return False
    cur = admin.cursor()
    steps = [
        (f"SELECT 1 FROM pg_roles WHERE rolname='{PG_USER}'",
         f"CREATE ROLE {PG_USER} LOGIN PASSWORD '{PG_PASS}'"),
        (f"SELECT 1 FROM pg_database WHERE datname='{PG_DB}'",
         f"CREATE DATABASE {PG_DB} OWNER {PG_USER}"),
    ]
    for probe, ddl in steps:
        cur.execute(probe)
        if not cur.fetchone():
            try:
                cur.execute(ddl)
                ok(f"Executed: {ddl}")
            except Exception as e:
                warn(f"{ddl} -> {e}")
    db_conn = try_psql_connect(PG_USER, PG_DB, PG_PASS) or admin
    try:
        c2 = db_conn.cursor()
        c2.execute(f"GRANT ALL ON SCHEMA public TO {PG_USER}")
        try:
            c2.execute("CREATE EXTENSION IF NOT EXISTS vector")
            ok("pgvector extension enabled (used by self-hosted RAG tutor).")
        except Exception:
            info("pgvector not available — FAISS fallback will be used by the ML service.")
    except Exception:
        pass
    finally:
        if db_conn is not admin:
            db_conn.close()
        admin.close()
    ok(f"Database '{PG_DB}' ready for user '{PG_USER}'.")
    return True


def precreate_tables():
    py = venv_python(BACKEND_DIR)
    if not py.exists():
        return
    info("Ensuring database tables exist (Base.metadata.create_all) ...")
    # NOTE: sys.exit(...) inside `python -c` raises SystemExit which asyncio
    # wraps into a Task exception and dumps a 100-line traceback. We catch it
    # explicitly so a missing/unreachable PostgreSQL prints ONE clean line
    # instead of scaring the user with a stack trace.
    code = (
        "import asyncio, sys\n"
        "try:\n"
        "    import app.models, app.database as d\n"
        "    async def _go():\n"
        "        await d.init_db()\n"
        "        await d.engine.dispose()\n"
        "    asyncio.run(_go())\n"
        "    print('tables ready')\n"
        "except SystemExit:\n"
        "    raise\n"
        "except BaseException as e:\n"
        "    print('DB-NOT-READY:', type(e).__name__, str(e).splitlines()[0][:200])\n"
        "    sys.exit(3)\n"
    )
    p = run([str(py), "-c", code], cwd=BACKEND_DIR, capture=True, check=False)
    if p.returncode == 0:
        ok("Tables created / verified.")
    else:
        warn("Could not pre-create tables (PostgreSQL not reachable yet) — the"
             " backend will still start; DB endpoints stay degraded until you"
             " follow STEP 3 in README.md and re-run python runner.py.")


# ------------------------------------------------------------------ watchdog
def _restart_service(name: str):
    """Restart one named service after a crash.

    start_*() append the new Popen to PROCESSES themselves, so we must NOT
    re-append here (that used to register the same child twice). Returns the
    new Popen or None if the restart failed / never became ready.
    """
    try:
        if name == "backend":
            return start_backend()
        if name == "frontend":
            return start_frontend()
        if name == "ml_service":
            return start_ml()
    except Exception as e:
        warn(f"Auto-restart of {name} failed: {e}")
        return None


def monitor_loop(max_restarts_per_service: int = 5):
    """Keep-alive supervisor: watches child processes, restarts crashes with
    exponential backoff, exits only when everything is dead and unrecoverable.
    """
    counts = {"backend": 0, "frontend": 0, "ml_service": 0}
    while True:
        time.sleep(3)
        for p in list(PROCESSES):
            rc = p.poll()
            if rc is None:
                continue
            n = getattr(p, "_vj_name", None)
            if p in PROCESSES:
                PROCESSES.remove(p)
            if n and counts.get(n, 0) < max_restarts_per_service:
                counts[n] += 1
                wait_s = min(2 ** counts[n], 30)
                warn(f"{n} exited (code {rc}) — auto-restarting in {wait_s}s "
                     f"(attempt {counts[n]}/{max_restarts_per_service}). "
                     f"See .vj_logs/{n}.log")
                time.sleep(wait_s)
                newp = _restart_service(n)
                if newp is None or newp.poll() is not None:
                    alive = [q for q in PROCESSES if q.poll() is None]
                    fail(f"{n} could NOT be restarted ({len(alive)} services still alive).")
            elif n:
                fail(f"{n} crashed {max_restarts_per_service}x — giving up on it."
                     f" Check .vj_logs/{n}.log")
        if not PROCESSES:
            break
        if all(q.poll() is not None for q in PROCESSES):
            # give the loop one more sweep to attempt restarts before quitting
            if all(getattr(q, "_reported", False) for q in PROCESSES):
                break
            for q in PROCESSES:
                q._reported = True


def step3_postgres(do_install: bool):
    banner("STEP 3 — PostgreSQL bootstrap")
    if not port_open(PG_PORT):
        if do_install and install_postgres():
            ok("PostgreSQL is now listening on port 5432.")
        else:
            warn(f"No PostgreSQL server reachable at localhost:{PG_PORT}.\n"
                 "\nThe app will still start, but DB-backed endpoints will fail.\n"
                 "Start PostgreSQL first, e.g.:\n"
                 "  • Ubuntu/Debian   :  sudo service postgresql start\n"
                 "  • macOS (Homebrew):  brew services start postgresql@16\n"
                 "  • Windows         :  installer service 'postgresql-x64-16', or:\n"
                 "                         winget install -e --accept-package-agreements PostgreSQL.16\n"
                 "  • Docker          :  docker run -d --name vj-pg -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16\n"
                 "Then re-run: python runner.py")
            return
    else:
        ok("PostgreSQL is reachable on port 5432.")
    bootstrap_database()
    precreate_tables()


# ------------------------------------------------------------------ STEP 4
def start_service(name: str, cmd, cwd: Path, port: int, env_extra=None, shell=False,
                  ready_url: str | None = None):
    """Launch a service and wait until it is GENUINELY READY.

    A listening socket alone is not enough (uvicorn binds the port before the
    app finishes importing — that made ml_service look 'STARTING forever' and
    let half-dead servers pass as UP). We now poll an actual HTTP endpoint
    when one is given, and also detect early process exit with log tails.
    """
    banner(f"STEP 4 — Starting {name}")
    LOG_DIR.mkdir(exist_ok=True)
    log = LOG_DIR / f"{name}.log"
    run_env = os.environ.copy()
    if env_extra:
        run_env.update(env_extra)
    display = cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)
    info(f"Command : {display}")
    info(f"Workdir : {cwd}")
    info(f"Logfile : {log.relative_to(ROOT)}")
    fh = open(log, "w", encoding="utf-8", errors="ignore")
    kwargs = dict(cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT, env=run_env, shell=shell)
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    proc._vj_name = name          # used by monitor_loop to identify crashes
    proc._vj_cmdline = display    # fallback identification
    PROCESSES.append(proc)

    def tail_lines(n=15):
        try:
            return "".join(open(log, encoding="utf-8", errors="ignore").readlines()[-n:])
        except Exception:
            return "(no log output yet)"

    deadline = time.time() + 90
    while time.time() < deadline:
        # Real readiness check: HTTP endpoint if provided, else just the port.
        ready = http_ok(ready_url) if ready_url else port_open(port)
        if ready:
            ok(f"{name} is UP on port {port}"
               + (f"  →  {ready_url}" if ready_url else f"  →  http://localhost:{port}"))
            return proc
        # Process died early? Report immediately instead of waiting 90 s.
        if proc.poll() is not None:
            warn(f"{name} EXITED early (code {proc.returncode}). Last log lines:\n{tail_lines()}")
            return proc
        time.sleep(0.5)

    warn(f"{name} did NOT become ready within 90s. Last log lines:\n{tail_lines()}")
    return proc


def start_backend():
    py = venv_python(BACKEND_DIR)
    exe = str(py) if py.exists() else sys.executable
    kill_port(BACKEND_PORT)
    return start_service("backend",
                  [exe, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
                  BACKEND_DIR, BACKEND_PORT,
                  ready_url=f"http://127.0.0.1:{BACKEND_PORT}/api/health")


def _fetch_page(url: str, timeout: float = 5.0):
    """Return (status, body) for a URL; (0, '') on any failure."""
    try:
        from urllib.request import urlopen
        with urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="ignore")
    except Exception:
        return 0, ""


def _find_free_port(preferred: int) -> int:
    """The configured port, or the next free one if something else owns it
    (a stale server used to make Vite silently move to :3001 while the
    runner kept probing :3000 — 'frontend never ready' false alarm)."""
    for p in range(preferred, preferred + 20):
        if not port_open(p):
            return p
    return preferred


def start_frontend():
    """Windows-safe launch. Never passes quoted paths through cmd strings —
    that produced: '\"C:\\Program Files\\nodejs\\npm.cmd\"' is not recognized.

    Readiness here means MORE than an open port: we fetch '/' and verify the
    HTML actually contains the #root mount node AND that /src/main.tsx plus
    EVERY view module compile (HTTP 200). A Vite server that boots but serves
    a page whose entry module has a syntax error would otherwise look fine
    while the browser shows a blank white screen.

    Also: OneDrive-synced project folders intermittently lock/rewrite files
    under node_modules, which breaks Vite's dependency optimizer with random
    MODULE_NOT_FOUND / EBUSY errors → blank page. We detect the folder on
    Windows and route Vite's cache to %LOCALAPPDATA% to dodge it.
    """
    kill_port(FRONTEND_PORT)
    port = _find_free_port(FRONTEND_PORT)
    if port != FRONTEND_PORT:
        warn(f"Port {FRONTEND_PORT} is occupied by another program — using "
             f"{port} instead so the frontend can still start.")
    base_url = f"http://127.0.0.1:{port}"

    modules_to_check = ["/src/main.tsx", "/src/App.tsx",
                        "/src/views/LoginPage.tsx",
                        "/src/views/OnboardingQuiz.tsx",
                        "/src/views/Dashboard.tsx"]

    def broken_module():
        """Return (module, status, snippet) of the first module Vite fails to
        transform, else None."""
        for m in modules_to_check:
            st, body = _fetch_page(base_url + m)
            if st == 200:
                continue
            if st >= 500 or "Transform failed" in body or "[plugin" in body \
                    or "Internal server error" in body:
                return m, st, body[:400]
        return None

    def frontend_ready(proc) -> bool:
        if proc is not None and proc.poll() is not None:
            return False
        status, html = _fetch_page(base_url + "/")
        if '<div id="root"' not in html or "/src/main.tsx" not in html:
            return False
        return broken_module() is None

    def wait_until_ready(proc, seconds: float = 30.0) -> bool:
        """Vite can answer the port a beat before it serves valid HTML — poll."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if frontend_ready(proc):
                return True
            if proc is not None and proc.poll() is not None:
                return False
            time.sleep(0.5)
        return False

    def report_success(via: str):
        global ACTUAL_FRONTEND_PORT
        ACTUAL_FRONTEND_PORT = port
        ok(f"Frontend verified via {via}: real HTML with #root + main.tsx + "
           f"all views compile (NOT a blank page) → http://localhost:{port}")

    # --- environment tweaks ----------------------------------------------------
    fe_env = {}
    path_l = str(FRONTEND_DIR).lower()
    if IS_WINDOWS and ("onedrive" in path_l or "one drive" in path_l):
        cache = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) \
              / "vidyajyoti" / "vite-cache"
        cache.mkdir(parents=True, exist_ok=True)
        fe_env["VITE_CACHE_DIR"] = str(cache)
        warn("Project lives inside a OneDrive folder — moved Vite's dep cache "
             f"to {cache} to avoid sync-lock corruption (blank-screen bug).")
        warn("TIP: right-click the project folder in File Explorer → 'Always "
             "keep on this device', or move it out of OneDrive for reliability.")

    def diagnose_failure():
        bad = broken_module()
        if bad:
            m, st, snip = bad
            warn(f"Blank-screen cause found: {m} fails to compile "
                 f"(HTTP {st}). Vite error:\n{snip}")
        else:
            _, html = _fetch_page(base_url + "/")
            warn("Frontend port open but page incomplete "
                 f"(first 200 chars: {html[:200]!r}). Check .vj_logs/frontend.log"
                 " — usually corrupted node_modules: delete it and re-run runner.py.")

    # Preferred: run Vite directly with node — no .cmd shim involved at all.
    node = shutil.which("node") or shutil.which("node.exe") \
        or next((p for p in [r"C:\Program Files\nodejs\node.exe", "/usr/local/bin/node", "/usr/bin/node"]
                 if Path(p).exists()), None)
    vite_js = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"

    if node and vite_js.exists():
        proc = start_service(
            "frontend",
            [node, str(vite_js), "--host", "0.0.0.0", "--port", str(port)],
            FRONTEND_DIR, port, env_extra=fe_env or None,
        )
        if wait_until_ready(proc):
            report_success("direct node+vite.js")
            return proc
        # fall through to npm
        diagnose_failure()
        try:
            proc.terminate()
        except Exception:
            pass
        if proc in PROCESSES:
            PROCESSES.remove(proc)
        kill_port(port)
        warn("Direct Vite launch failed — retrying via npm ...")

    npm, _ = find_npm()
    if npm:
        # Absolute path, NO extra quotes, list form works for .cmd on Windows too.
        proc = start_service("frontend", [npm, "run", "dev"], FRONTEND_DIR, port,
                             env_extra=fe_env or None)
        if wait_until_ready(proc):
            report_success("npm run dev")
        else:
            diagnose_failure()
        return proc
    else:
        fail("Neither node nor npm available to start the frontend.")


def start_ml():
    py = venv_python(ML_DIR)
    exe = str(py) if py.exists() else sys.executable
    kill_port(ML_PORT)

    # Pre-flight: import app_ml.main in a throwaway process BEFORE uvicorn
    # starts. If there is any syntax/import error, we print the REAL traceback
    # here instead of letting uvicorn sit at "Waiting for application startup"
    # (which looked like 'ml_service stuck in STARTING forever').
    check = subprocess.run([exe, "-c", "import app_ml.main"],
                           cwd=str(ML_DIR), capture_output=True, text=True)
    if check.returncode != 0:
        warn("app_ml.main failed to import — showing the actual error:")
        print((check.stderr or check.stdout)[-2500:])
        warn("Attempting to start anyway; /api/ml/health may stay down until fixed.")

    # Fast, deterministic startup: app_ml.main imports ONLY fastapi/stdlib —
    # no pip installs and no heavy model imports happen inside the server
    # process anymore (that was the 'stuck in STARTING forever' bug). Heavy
    # deps install in a background thread by STEP 2; endpoints use local
    # heuristic fallbacks until they land.
    proc = start_service("ml_service",
                         [exe, "-m", "uvicorn", "app_ml.main:app", "--host", "0.0.0.0",
                          "--port", str(ML_PORT), "--timeout-graceful-shutdown", "3"],
                         ML_DIR, ML_PORT,
                         ready_url=f"http://127.0.0.1:{ML_PORT}/api/ml/health")
    return proc


# ------------------------------------------------------------------ dashboard
def status_dashboard():
    banner("VidyaJyoti — STATUS DASHBOARD")
    fe_url = f"http://localhost:{ACTUAL_FRONTEND_PORT}"
    rows = [("backend", BACKEND_PORT, "http://localhost:8000/api/health"),
            ("frontend", ACTUAL_FRONTEND_PORT, fe_url),
            ("ml_service", ML_PORT, "http://localhost:9000/api/ml/health")]
    for name, port, url in rows:
        # Verify with a real HTTP request, not just an open socket —
        # no more false "RUNNING" for servers that never finished starting.
        state = "RUNNING" if http_ok(url.replace("localhost", "127.0.0.1")) else "STOPPED"
        extra = ""
        if name == "frontend" and state == "RUNNING":
            _, html = _fetch_page(f"http://127.0.0.1:{port}/")
            if '<div id="root"' not in html or "/src/main.tsx" not in html:
                state = "BROKEN PAGE"
                extra = "  (serving HTML without #root — check frontend.log)"
            else:
                for m in ("/src/main.tsx", "/src/App.tsx",
                          "/src/views/LoginPage.tsx",
                          "/src/views/OnboardingQuiz.tsx",
                          "/src/views/Dashboard.tsx"):
                    st_mod, body = _fetch_page(f"http://127.0.0.1:{port}{m}")
                    if st_mod >= 500 or "Transform failed" in body or "[plugin" in body:
                        state = "BROKEN PAGE"
                        extra = f"  ({m} fails to compile — blank screen; see frontend.log / browser console F12)"
                        break
        print(f"  {name:<12} {state:<12} {url}{extra}")
    print("\n  ➜ Frontend (open this!)  " + fe_url)
    print("  ➜ Backend API            http://localhost:8000/api/health")
    print("  ➜ Backend docs (Swagger) http://localhost:8000/docs")
    print("  ➜ Self-hosted ML service http://localhost:9000/api/ml/health")
    heavy_log = LOG_DIR / "ml_heavy_install.log"
    if heavy_log.exists():
        try:
            last = [l for l in heavy_log.read_text(
                encoding="utf-8", errors="ignore").splitlines() if l.strip()][-1:]
            if last and ("SKIP" in last[0] or "FAILED" in last[0]):
                print("  ℹ Heavy ML stack (torch/faiss) optional — "
                      "ML service runs on self-hosted pure-Python models now.")
        except Exception:
            pass
    print(f"\n  Logs folder : {LOG_DIR.name}/")
    print("  Press Ctrl+C to stop ALL services cleanly.\n")
    try:
        webbrowser.open(fe_url)
    except Exception:
        pass


def shutdown(sig=None, frame=None):
    print("\nShutting down all VidyaJyoti services ...")
    for p in PROCESSES:
        if p.poll() is None:
            info(f"Stopping {p.pid} ...")
            try:
                if IS_WINDOWS:
                    subprocess.call(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    p.terminate()
            except Exception:
                pass
    ok("All services stopped. Bye! 👋")
    sys.exit(0)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="One-click VidyaJyoti launcher")
    ap.add_argument("--setup", action="store_true", help="install/check deps only")
    ap.add_argument("--no-ml", action="store_true", help="skip ML service")
    ap.add_argument("--no-install", action="store_true", help="skip node/postgres auto-install")
    ap.add_argument("--backend-only", action="store_true")
    ap.add_argument("--frontend-only", action="store_true")
    ap.add_argument("--ml-only", action="store_true")
    args = ap.parse_args()

    print("\n🪔  VIDYAJYOTI — ONE-CLICK RUNNER  (no external AI APIs — fully self-hosted)")

    step0_python_modules()
    fe_ok = step1_node(do_install=not args.no_install)
    step2_service_deps("backend", BACKEND_DIR)
    if not args.no_ml:
        step2_service_deps("ml_service", ML_DIR)
    step3_postgres(do_install=not args.no_install)

    if args.setup:
        ok("Setup complete. Re-run without --setup to start the servers.")
        return

    import signal
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    only = args.backend_only or args.frontend_only or args.ml_only
    if not only or args.backend_only:
        start_backend()
    if (not only or args.frontend_only) and fe_ok:
        start_frontend()
    if (not only or args.ml_only) and not args.no_ml:
        start_ml()

    status_dashboard()

    # Keep alive; auto-restart crashes (ml_service "STARTING forever" /
    # frontend dying after OneDrive corrupted node_modules used to leave the
    # runner exiting with a dead dashboard — now it supervises instead).
    monitor_loop()
    fail("All services stopped. See logs in .vj_logs/ and re-run python runner.py.")


if __name__ == "__main__":
    main()
