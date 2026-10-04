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


def step0_python_modules():
    banner("STEP 0 — Checking required Python modules")
    ensure_module("psutil")
    ensure_module("psycopg2", "psycopg2-binary")


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
    if not nm.exists() or not lock.exists():
        info("Installing frontend dependencies (npm install) ...")
        rc = run([npm, "install"], cwd=FRONTEND_DIR, shell=IS_WINDOWS).returncode
        if rc != 0:
            fail("npm install failed.")
            return False
        ok("Frontend dependencies installed.")
    else:
        ok("Frontend dependencies already installed.")
    return True


# ------------------------------------------------------------------ STEP 2
def step2_service_deps(name: str, base: Path):
    banner(f"STEP 2 — Installing '{name}' Python dependencies")
    req = base / "requirements.txt"
    if not req.exists():
        warn(f"No requirements.txt for {name} — skipping.")
        return
    py = venv_python(base)
    if not py.exists():
        info(f"Creating virtual environment at {base / '.venv'} ...")
        run([sys.executable, "-m", "venv", str(base / ".venv")])
    stamp = base / ".venv" / ".deps_installed"
    if stamp.exists() and stamp.stat().st_mtime >= req.stat().st_mtime:
        ok(f"'{name}' dependencies already installed.")
        return
    info(f"pip installing {req.name} into .venv (first time may download a lot) ...")
    # Prebuilt wheels only — avoids pg_config / source-build failures on Windows.
    rc = run([str(py), "-m", "pip", "install", "--only-binary=:all:",
              "--upgrade", "pip"], check=False).returncode
    rc = run([str(py), "-m", "pip", "install", "-r", str(req)]).returncode
    if rc != 0:
        fail(f"pip install failed for '{name}'. Re-run runner.py after fixing.")
        sys.exit(1)
    stamp.touch()
    ok(f"'{name}' dependencies installed.")


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
    code = ("import asyncio, app.models, app.database as d;"
            "asyncio.run(d.init_db()); print('tables ready')")
    p = run([str(py), "-c", code], cwd=BACKEND_DIR, capture=True, check=False)
    if p.returncode == 0:
        ok("Tables created / verified.")
    else:
        warn("Could not pre-create tables — backend will still start;"
             " DB endpoints may fail until the schema exists.")


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
def start_service(name: str, cmd, cwd: Path, port: int, env_extra=None, shell=False):
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
    PROCESSES.append(proc)
    if wait_for_port(port, 90):
        ok(f"{name} is UP on port {port}  →  http://localhost:{port}")
        return proc
    tail = ""
    try:
        tail = "".join(open(log, encoding="utf-8", errors="ignore").readlines()[-15:])
    except Exception:
        pass
    warn(f"{name} did NOT come up on port {port}. Last log lines:\n{tail}")
    return proc


def start_backend():
    py = venv_python(BACKEND_DIR)
    exe = str(py) if py.exists() else sys.executable
    kill_port(BACKEND_PORT)
    start_service("backend",
                  [exe, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
                  BACKEND_DIR, BACKEND_PORT)


def start_frontend():
    """Windows-safe launch. Never passes quoted paths through cmd strings —
    that produced: '\"C:\\Program Files\\nodejs\\npm.cmd\"' is not recognized."""
    kill_port(FRONTEND_PORT)

    # Preferred: run Vite directly with node — no .cmd shim involved at all.
    node = shutil.which("node") or shutil.which("node.exe") \
        or next((p for p in [r"C:\Program Files\nodejs\node.exe", "/usr/local/bin/node", "/usr/bin/node"]
                 if Path(p).exists()), None)
    vite_js = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"

    if node and vite_js.exists():
        proc = start_service(
            "frontend",
            [node, str(vite_js), "--host", "0.0.0.0", "--port", str(FRONTEND_PORT)],
            FRONTEND_DIR, FRONTEND_PORT,
        )
        if proc and port_open(FRONTEND_PORT):
            return proc
        # fall through to npm
        PROCESSES.remove(proc) if proc in PROCESSES else None
        warn("Direct Vite launch failed — retrying via npm ...")

    npm, _ = find_npm()
    if npm:
        # Absolute path, NO extra quotes, list form works for .cmd on Windows too.
        start_service("frontend", [npm, "run", "dev"], FRONTEND_DIR, FRONTEND_PORT)
    else:
        fail("Neither node nor npm available to start the frontend.")


def start_ml():
    py = venv_python(ML_DIR)
    exe = str(py) if py.exists() else sys.executable
    kill_port(ML_PORT)
    start_service("ml_service",
                  [exe, "-m", "uvicorn", "app_ml.main:app", "--host", "0.0.0.0", "--port", str(ML_PORT)],
                  ML_DIR, ML_PORT)


# ------------------------------------------------------------------ dashboard
def status_dashboard():
    banner("VidyaJyoti — STATUS DASHBOARD")
    rows = [("backend", BACKEND_PORT, "http://localhost:8000/api/health"),
            ("frontend", FRONTEND_PORT, "http://localhost:3000"),
            ("ml_service", ML_PORT, "http://localhost:9000/api/ml/health")]
    for name, port, url in rows:
        state = "RUNNING" if port_open(port) else "STOPPED"
        print(f"  {name:<12} {state:<10} {url}")
    print("\n  ➜ Frontend (open this!)  http://localhost:3000")
    print("  ➜ Backend API            http://localhost:8000/api/health")
    print("  ➜ Backend docs (Swagger) http://localhost:8000/docs")
    print("  ➜ Self-hosted ML service http://localhost:9000/api/ml/health")
    print(f"\n  Logs folder : {LOG_DIR.name}/")
    print("  Press Ctrl+C to stop ALL services cleanly.\n")
    try:
        webbrowser.open("http://localhost:3000")
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

    # Keep alive; report crashes.
    while True:
        time.sleep(3)
        for p in list(PROCESSES):
            if p.poll() is not None and getattr(p, "_reported", False) is False:
                p._reported = True
                fail("A service exited unexpectedly — check .vj_logs/")
        if all(p.poll() is not None for p in PROCESSES) and PROCESSES:
            break


if __name__ == "__main__":
    main()
