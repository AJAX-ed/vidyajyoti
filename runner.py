#!/usr/bin/env python3
"""
VidyaJyoti — One-Click Runner
=============================

Running this single file starts EVERYTHING you need:

    python runner.py            # start all services (default)
    python runner.py --setup    # only install dependencies / check env, then exit
    python runner.py --no-ml    # skip the self-hosted ML service (port 9000)
    python runner.py --backend-only / --frontend-only / --ml-only

What it does automatically (no manual steps needed):
  1. Ensures required Python modules are installed
     (psutil to manage processes, psycopg2-binary for DB bootstrap).
     If a module is missing it runs:  python -m pip install <module>
  2. Checks Node.js is available and installs frontend deps
     (npm install) if node_modules/ is missing.
  3. Installs backend requirements.txt (and ML requirements.txt if enabled).
  4. Bootstraps PostgreSQL: creates user `vj_user`, database `vidyajyoti`
     and grants the needed schemas — but ONLY if they don't already exist.
     (If Postgres isn't reachable it prints clear setup instructions instead.)
  5. Launches all servers as background processes with live log files:
        • Backend   : uvicorn app.main:app      -> http://localhost:8000
        • Frontend  : npm run dev (vite)        -> http://localhost:3000
        • ML service: uvicorn app_ml.main:app   -> http://localhost:9000
  6. Health-checks every service and shows a status dashboard.
  7. Keeps running; press Ctrl+C once to shut everything down cleanly.

NO EXTERNAL AI APIs are used anywhere — the ML service runs self-hosted models.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ----------------------------------------------------------------------------
# Paths & configuration
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "vidyajyoti" / "frontend"
BACKEND_DIR = ROOT / "vidyajyoti" / "backend"
ML_DIR = ROOT / "vidyajyoti" / "backend_ml"
LOG_DIR = ROOT / ".vj_logs"

PYTHON = sys.executable  # the interpreter used to run this script

DB_HOST, DB_PORT = "localhost", 5432
DB_USER, DB_PASS, DB_NAME = "vj_user", "vj_password", "vidyajyoti"

BACKEND_PORT, FRONTEND_PORT, ML_PORT = 8000, 3000, 9000


def load_env_file():
    """Load vidyajyoti/.env into os.environ (without overriding existing vars)."""
    env_path = ROOT / "vidyajyoti" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


load_env_file()

# ANSI colors
G, R, Y, C, B, X = ("\033[92m", "\033[91m", "\033[93m",
                    "\033[96m", "\033[1m", "\033[0m")


def ok(msg):    print(f"{G}[ OK ]{X} {msg}")
def warn(msg):  print(f"{Y}[WARN]{X} {msg}")
def err(msg):   print(f"{R}[FAIL]{X} {msg}")
def info(msg):  print(f"{C}[INFO]{X} {msg}")
def head(msg):  print(f"\n{B}{'=' * 70}{X}\n{B} {msg}{X}\n{B}{'=' * 70}{X}")


# ----------------------------------------------------------------------------
# STEP 0 — Ensure required PYTHON modules (install on demand, as requested)
# ----------------------------------------------------------------------------
def ensure_module(import_name, pip_name=None):
    """try: import <module>  except: pip install it, then import again."""
    pip_name = pip_name or import_name
    try:
        __import__(import_name)
        return True
    except ImportError:
        warn(f"Python module '{import_name}' not found — installing via pip ...")
        ret = subprocess.run(
            [PYTHON, "-m", "pip", "install", "--quiet", pip_name]
        ).returncode
        if ret != 0:
            err(f"Could not install '{pip_name}'. Install it manually: "
                f"{PYTHON} -m pip install {pip_name}")
            return False
        try:
            __import__(import_name)
            ok(f"Module '{import_name}' installed successfully.")
            return True
        except ImportError:
            err(f"'{pip_name}' installed but import still fails.")
            return False


HAS_PSUTIL = False
HAS_PSYCOPG2 = False


def check_python_modules(with_db=True):
    global HAS_PSUTIL, HAS_PSYCOPG2
    head("STEP 0 — Checking required Python modules")
    HAS_PSUTIL = ensure_module("psutil")
    if with_db:
        HAS_PSYCOPG2 = ensure_module("psycopg2", "psycopg2-binary")


# ----------------------------------------------------------------------------
# Helpers: ports, killing stale processes
# ----------------------------------------------------------------------------
def port_in_use(port):
    if HAS_PSUTIL:
        try:
            import psutil
            return bool(psutil.net_connections(kind="inet")) and any(
                c.status == "LISTEN" and c.laddr and c.laddr.port == port
                for c in psutil.net_connections(kind="inet")
            )
        except Exception:
            pass
    # fallback: try to open a socket
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def kill_port(port):
    """Free a port by terminating the LISTENing process (our own stale servers)."""
    if not HAS_PSUTIL:
        warn("psutil unavailable — cannot auto-free port "
             f"{port}. Kill it manually if startup fails.")
        return
    import psutil
    for c in psutil.net_connections(kind="inet"):
        if c.status == "LISTEN" and c.laddr and c.laddr.port == port and c.pid:
            try:
                p = psutil.Process(c.pid)
                info(f"Killing stale process on port {port}: "
                     f"PID {c.pid} ({p.name()})")
                p.terminate()
                try:
                    p.wait(timeout=5)
                except psutil.NoSuchProcess:
                    pass
            except psutil.Error:
                pass


# ----------------------------------------------------------------------------
# STEP 1 — Node.js / frontend dependencies
# ----------------------------------------------------------------------------
def check_node_and_frontend():
    head("STEP 1 — Node.js & frontend dependencies")
    if not shutil.which("node") or not shutil.which("npm"):
        err("Node.js / npm not found on PATH.")
        print("   Install the latest LTS from https://nodejs.org and re-run this file.")
        sys.exit(1)
    ok(f"Node.js found: {subprocess.run(['node', '-v'], capture_output=True, text=True).stdout.strip()}")

    if not FRONTEND_DIR.exists():
        err(f"Frontend folder not found at {FRONTEND_DIR}")
        sys.exit(1)

    if not (FRONTEND_DIR / "node_modules").exists():
        info("node_modules missing — running 'npm install' (this can take a while) ...")
        ret = subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                             cwd=FRONTEND_DIR).returncode
        if ret != 0:
            err("npm install failed. Fix the errors above and re-run runner.py")
            sys.exit(1)
        ok("Frontend dependencies installed.")
    else:
        ok("Frontend dependencies already installed.")


# ----------------------------------------------------------------------------
# STEP 2 — Python venvs + backend / ML requirements
# ----------------------------------------------------------------------------
def venv_pip_and_python(venv_dir):
    if os.name == "nt":
        return (venv_dir / "Scripts" / "python.exe"), (venv_dir / "Scripts" / "python.exe")
    return (venv_dir / "bin" / "python"), (venv_dir / "bin" / "python")


def install_requirements(name, workdir, marker_pkg):
    """Create .venv inside workdir (if needed) and pip install requirements.txt."""
    head(f"STEP 2 — Installing '{name}' Python dependencies")
    venv_dir = workdir / ".venv"
    py, _ = venv_pip_and_python(venv_dir)
    if not py.exists():
        info(f"Creating virtual environment at {venv_dir.relative_to(ROOT)} ...")
        ret = subprocess.run([PYTHON, "-m", "venv", str(venv_dir)]).returncode
        if ret != 0:
            err("Failed to create venv.")
            sys.exit(1)
    check = subprocess.run([str(py), "-c", f"import {marker_pkg}"],
                           capture_output=True)
    if check.returncode == 0:
        ok(f"'{name}' dependencies already installed.")
    else:
        req = workdir / "requirements.txt"
        info(f"pip installing {req.relative_to(ROOT)} into {venv_dir.name} "
             "(be patient — first time may download a lot) ...")
        ret = subprocess.run(
            [str(py), "-m", "pip", "install", "--upgrade", "pip", "--quiet"]
        ).returncode
        ret = subprocess.run(
            [str(py), "-m", "pip", "install", "-r", str(req)]
        ).returncode
        if ret != 0:
            err(f"pip install failed for '{name}'. Re-run runner.py after fixing.")
            sys.exit(1)
        ok(f"'{name}' dependencies installed.")
    return py


# ----------------------------------------------------------------------------
# STEP 3 — Bootstrap PostgreSQL (create user + db if missing)
# ----------------------------------------------------------------------------
def bootstrap_postgres():
    head("STEP 3 — PostgreSQL bootstrap")
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        if s.connect_ex((DB_HOST, DB_PORT)) != 0:
            warn(f"No PostgreSQL server reachable at {DB_HOST}:{DB_PORT}.")
            print(textwrap_dedent(f'''
                 {Y}The app will still start, but DB-backed endpoints will fail.
                 Start PostgreSQL first, e.g.:
                   • macOS (Homebrew):  brew services start postgresql@16
                   • Ubuntu/Debian   :  sudo service postgresql start
                   • Docker          :  docker run -d --name vj-pg \
-P 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
                   • Windows         :  start the "postgresql-x64-16" service
                 Then re-run: python runner.py{X}
            '''))
            return False

    if not HAS_PSYCOPG2:
        warn("psycopg2 unavailable — skipping automatic DB bootstrap. "
             "Follow README.md STEP 1 to create user/database manually.")
        return False

    import psycopg2
    from psycopg2 import sql, errors as pg_errors

    def connect(dbname, user, password):
        return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=dbname,
                                user=user, password=password)

    # Find superuser credentials to run CREATE ROLE / CREATE DATABASE
    admin_conn = None
    tried = []
    candidates = [("postgres", ""), ("postgres", "postgres"),
                  (os.getenv("USER", "postgres"), "")]
    for u, p in candidates:
        try:
            admin_conn = connect("postgres", u, p)
            ok(f"Connected to PostgreSQL as superuser '{u}'.")
            break
        except Exception:
            tried.append(f"{u}/{p or '(no password)'}")
    if admin_conn is None:
        env_url = os.getenv("DATABASE_URL", "")
        if "postgres://" in env_url or "postgresql://" in env_url.replace("+asyncpg", ""):
            try:
                admin_conn = connect("postgres", DB_USER, DB_PASS)
                ok(f"Connected using existing '{DB_USER}' role.")
            except Exception:
                pass
    if admin_conn is None:
        warn("Could not auto-connect as a PostgreSQL superuser "
             f"(tried: {', '.join(tried)}).")
        print(textwrap_dedent(f'''
             {Y}Create the user & database manually (see README.md STEP 1):
               sudo -u postgres psql -c "CREATE ROLE vj_user LOGIN PASSWORD 'vj_password';"
               sudo -u postgres psql -c "CREATE DATABASE vidyajyoti OWNER vj_user;"
               sudo -u postgres psql -d vidyajyoti -c "GRANT ALL ON SCHEMA public TO vj_user;"
               sudo -u postgres psql -d vidyajyoti -c "ALTER SCHEMA public OWNER TO vj_user;"
             Then re-run: python runner.py{X}
        '''))
        return False

    admin_conn.autocommit = True
    cur = admin_conn.cursor()
    try:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (DB_USER,))
        if cur.fetchone():
            ok(f"Role '{DB_USER}' already exists.")
        else:
            cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(DB_USER), sql.Literal(DB_PASS)))
            ok(f"Created role '{DB_USER}'.")

        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DB_NAME,))
        if cur.fetchone():
            ok(f"Database '{DB_NAME}' already exists.")
        else:
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(DB_NAME), sql.Identifier(DB_USER)))
            ok(f"Created database '{DB_NAME}' owned by '{DB_USER}'.")

        cur.close()
        admin_conn.close()

        # Owner-side grants (public schema) — connect AS vj_user to the new db
        try:
            db_conn = connect(DB_NAME, DB_USER, DB_PASS)
            db_conn.autocommit = True
            dcur = db_conn.cursor()
            grant_sql = sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(
                sql.Identifier(DB_USER))
            owner_sql = sql.SQL("ALTER SCHEMA public OWNER TO {}").format(
                sql.Identifier(DB_USER))
            try:
                dcur.execute(grant_sql)
                try:
                    dcur.execute(owner_sql)  # only works if we own it / are superuser
                except pg_errors.InsufficientPrivilege:
                    info("Could not transfer 'public' schema ownership "
                         "(harmless on PG15+ if CREATE granted).")
                dcur.close()
                db_conn.close()
                ok("Schema grants applied for vj_user.")
            finally:
                try:
                    db_conn.close()
                except Exception:
                    pass

            # Try pgvector extension via a superuser session (optional)
            try:
                ext_conn = connect(DB_NAME, DB_USER, DB_PASS)
                ext_conn.autocommit = True
                ecur = ext_conn.cursor()
                ecur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                ecur.close()
                ext_conn.close()
                ok("pgvector extension enabled (for ML service embeddings).")
            except Exception:
                info("pgvector extension not available — OK (optional; FAISS "
                     "fallback is used by the self-hosted ML service).")
            return True
        except pg_errors.OperationalError as e:
            warn(f"Could not apply schema grants: {e}")
            print(textwrap_dedent(f'''
                 {Y}Run these ONCE as a PostgreSQL superuser, then re-execute runner.py:
                   psql -d vidyajyoti -c "GRANT ALL ON SCHEMA public TO vj_user;"
                   psql -d vidyajyoti -c "ALTER SCHEMA public OWNER TO vj_user;"{X}
            '''))
            return True  # user/db exist; backend may still work
    except Exception as e:
        err(f"PostgreSQL bootstrap error: {e}")
        return False


def textwrap_dedent(s):
    import textwrap
    return textwrap.dedent(s)


# ----------------------------------------------------------------------------
# STEP 4 — Launch servers
# ----------------------------------------------------------------------------
PROCESSES = {}  # name -> (Popen, url, logfile)


def wait_for_port(port, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use(port):
            return True
        time.sleep(1)
    return False


def start_service(name, cmd, cwd, port, env=None):
    head(f"STEP 4 — Starting {name}")
    LOG_DIR.mkdir(exist_ok=True)
    logfile = LOG_DIR / f"{name}.log"
    fh = open(logfile, "w")
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    info(f"Command : {' '.join(str(c) for c in cmd)}")
    info(f"Workdir : {cwd.relative_to(ROOT)}")
    info(f"Logfile : {logfile.relative_to(ROOT)}")
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=fh,
                            stderr=subprocess.STDOUT, env=run_env)
    PROCESSES[name] = (proc, port, logfile)
    if wait_for_port(port, timeout=90):
        ok(f"{name} is UP on port {port}  →  http://localhost:{port}")
    else:
        err(f"{name} did NOT come up on port {port}. Last log lines:")
        try:
            print("\n".join(logfile.read_text().splitlines()[-15:]))
        except Exception:
            pass


def start_backend(py_bin):
    start_service(
        "backend",
        [str(py_bin), "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
        BACKEND_DIR, BACKEND_PORT,
    )


def start_frontend():
    start_service(
        "frontend",
        ["npm", "run", "dev"],
        FRONTEND_DIR, FRONTEND_PORT,
    )


def start_ml(py_bin):
    start_service(
        "ml_service",
        [str(py_bin), "-m", "uvicorn", "app_ml.main:app",
         "--host", "0.0.0.0", "--port", str(ML_PORT)],
        ML_DIR, ML_PORT,
    )


# ----------------------------------------------------------------------------
# STEP 5 — Status dashboard & main loop
# ----------------------------------------------------------------------------
HEALTH_URLS = {
    "backend": f"http://localhost:{BACKEND_PORT}/api/health",
    "ml_service": f"http://localhost:{ML_PORT}/health",
}


def health_check(url, timeout=4):
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def show_dashboard(started_urls):
    head("VidyaJyoti — STATUS DASHBOARD")
    for name, (proc, port, logfile) in PROCESSES.items():
        alive = proc.poll() is None
        if name in HEALTH_URLS:
            healthy = health_check(HEALTH_URLS[name])
        else:  # frontend: vite serves on its port
            healthy = port_in_use(port)
        status = f"{G}RUNNING{X}" if (alive and healthy) else \
                 (f"{Y}STARTING{X}" if alive else f"{R}STOPPED{X}")
        print(f"  {name:<12} {status:<{18}}  http://localhost:{port}   "
              f"log: {Path(logfile).name}")
    print()
    for label, url in started_urls.items():
        print(f"  ➜ {label:<22} {url}")
    print(f"\n  Logs folder : {LOG_DIR.relative_to(ROOT)}/")
    print(f"  {B}Press Ctrl+C to stop ALL services cleanly.{X}\n")


def shutdown_all():
    print(f"\n{Y}Shutting down all VidyaJyoti services ...{X}")
    for name, (proc, port, _) in PROCESSES.items():
        if proc.poll() is None:
            info(f"Stopping {name} (PID {proc.pid}) ...")
            proc.terminate()
    deadline = time.time() + 8
    for name, (proc, _, _) in PROCESSES.items():
        remaining = max(0.1, deadline - time.time())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
    # also free ports of anything left behind
    for _, (_, port, _) in PROCESSES.items():
        kill_port(port)
    ok("All services stopped. Bye! 👋")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="One-click launcher for the full VidyaJyoti stack.")
    parser.add_argument("--setup", action="store_true",
                        help="Only install/check dependencies & DB, don't start servers.")
    parser.add_argument("--no-ml", action="store_true",
                        help="Skip the self-hosted ML service (port 9000).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--backend-only", action="store_true")
    group.add_argument("--frontend-only", action="store_true")
    group.add_argument("--ml-only", action="store_true")
    args = parser.parse_args()

    print(f"\n{B}{C}🪔  VIDYAJYOTI — ONE-CLICK RUNNER{X}{B}"
          f"  (no external AI APIs — fully self-hosted){X}\n")

    # STEP 0
    check_python_modules(with_db=not args.frontend_only)

    # STEP 1
    check_node_and_frontend()

    # STEP 2 — python deps
    backend_py = install_requirements("backend", BACKEND_DIR, "fastapi")
    ml_py = None
    if not args.no_ml and not args.backend_only and not args.frontend_only:
        ml_py = install_requirements("ml_service", ML_DIR, "fastapi")

    # STEP 3 — postgres bootstrap
    if not args.frontend_only and not args.ml_only:
        bootstrap_postgres()

    if args.setup:
        head("SETUP COMPLETE ✔  Run 'python runner.py' to start everything.")
        return

    # STEP 4 — start servers (free stale ports first)
    wanted = []
    if args.backend_only:
        wanted = ["backend"]
    elif args.frontend_only:
        wanted = ["frontend"]
    elif args.ml_only:
        wanted = ["ml_service"] if ml_py else []
        if not wanted:
            err("ML service disabled/deps missing. Use without --ml-only.")
            sys.exit(1)
    else:
        wanted = ["backend", "frontend"] + ([] if args.no_ml else ["ml_service"])

    for name in wanted:
        port = {"backend": BACKEND_PORT, "frontend": FRONTEND_PORT,
                "ml_service": ML_PORT}[name]
        if port_in_use(port):
            kill_port(port)
        time.sleep(0.5)
        if name == "backend":
            start_backend(backend_py)
        elif name == "frontend":
            start_frontend()
        elif name == "ml_service":
            start_ml(ml_py)

    urls = {}
    if "frontend" in PROCESSES:
        urls["Frontend (open this!)"] = f"http://localhost:{FRONTEND_PORT}"
    if "backend" in PROCESSES:
        urls["Backend API"] = f"http://localhost:{BACKEND_PORT}/api/health"
        urls["Backend docs (Swagger)"] = f"http://localhost:{BACKEND_PORT}/docs"
    if "ml_service" in PROCESSES:
        urls["Self-hosted ML service"] = f"http://localhost:{ML_PORT}/api/ml/health"

    show_dashboard(urls)

    # STEP 5 — keep alive until Ctrl+C
    try:
        while True:
            time.sleep(5)
            # surface crashed services
            for name, (proc, port, logfile) in PROCESSES.items():
                if proc.poll() is not None and not getattr(main, f"_warned_{name}", False):
                    setattr(main, f"_warned_{name}", True)
                    err(f"{name} exited unexpectedly (code {proc.returncode}). "
                        f"Check {logfile.name}")
    except KeyboardInterrupt:
        shutdown_all()


if __name__ == "__main__":
    main()
