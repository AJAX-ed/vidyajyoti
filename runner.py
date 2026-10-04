#!/usr/bin/env python3
"""
VidyaJyoti — One-Click Runner
=============================

Running this single file starts EVERYTHING you need:

    python runner.py            # start all services (default)
    python runner.py --setup    # only install dependencies / check env, then exit
    python runner.py --no-ml    # skip the self-hosted ML service (port 9000)
    python runner.py --backend-only / --frontend-only / --ml-only

This file is the ONLY thing you need to run. Even if you have NO npm
and NO PostgreSQL installed, it installs them itself:

  1. Ensures required Python modules are installed
     (psutil to manage processes, psycopg2-binary for DB bootstrap).
     If a module is missing it runs:  python -m pip install <module>
  2. Node.js / npm: if missing, auto-installs Node LTS —
       • Debian/Ubuntu (root or sudo): apt + NodeSource repo
       • macOS: Homebrew (if brew exists)
       • anything else: official tarball into ~/.local/node (PATH updated)
     Then runs `npm install` in frontend/ if node_modules/ is missing.
  3. PostgreSQL: if not installed locally, auto-installs it —
       • Debian/Ubuntu (root or sudo): apt-get install postgresql
       • macOS: brew install postgresql@16
       • no package manager available: spins up an official `postgres:16`
         Docker container instead
     Then bootstraps: creates role `vj_user`, database `vidyajyoti`,
     schema grants and optional pgvector — ONLY if they don't exist yet.
  4. Installs backend requirements.txt (and ML requirements.txt if enabled)
     into per-service virtual environments (.venv).
  5. Launches all servers as background processes with live log files:
        • Backend   : uvicorn app.main:app      -> http://localhost:8000
        • Frontend  : npm run dev (vite)        -> http://localhost:3000
        • ML service: uvicorn app_ml.main:app   -> http://localhost:9000
  6. Health-checks every service and shows a status dashboard.
  7. Keeps running; press Ctrl+C once to shut everything down cleanly.

Flags:
    --setup          only install/bootstrap everything, don't start servers
    --no-install     skip auto-installing Node.js / PostgreSQL (assume present)
    --no-ml          skip the self-hosted ML service (port 9000)
    --backend-only / --frontend-only / --ml-only

NO EXTERNAL AI APIs are used anywhere — the ML service runs self-hosted models.
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ----------------------------------------------------------------------------
# Paths & configuration
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent

def _find_project_base():
    """Locate the folder that contains frontend/ + backend/.

    Supports running runner.py from EITHER layout:
      repo-root/runner.py  +  repo-root/vidyajyoti/{frontend,backend,...}
      vidyajyoti/runner.py +  vidyajyoti/{frontend,backend,...}   (copy of repo)
    """
    for c in (ROOT / "vidyajyoti", ROOT):
        if (c / "backend").is_dir() and (c / "frontend").is_dir():
            return c
    return ROOT


BASE = _find_project_base()
FRONTEND_DIR = BASE / "frontend"
BACKEND_DIR = BASE / "backend"
ML_DIR = BASE / "backend_ml"
LOG_DIR = ROOT / ".vj_logs"


def rel(p):
    """Display a path relative to the runner's folder; never crashes."""
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)

PYTHON = sys.executable  # the interpreter used to run this script

DB_HOST, DB_PORT = "localhost", 5432
DB_USER, DB_PASS, DB_NAME = "vj_user", "vj_password", "vidyajyoti"

BACKEND_PORT, FRONTEND_PORT, ML_PORT = 8000, 3000, 9000


def load_env_file():
    """Load vidyajyoti/.env into os.environ (without overriding existing vars)."""
    env_path = next((c for c in (BASE / ".env", ROOT / "vidyajyoti" / ".env")
                     if c.exists()), None)
    if env_path is None:
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
    """try: import <module>  except: pip install it, then import again.

    Retries with --only-binary :all: so source builds (which need things
    like pg_config / C compilers that a fresh Windows machine doesn't have)
    are never attempted — pip will grab the prebuilt wheel instead.
    """
    pip_name = pip_name or import_name
    try:
        __import__(import_name)
        return True
    except ImportError:
        pass
    warn(f"Python module '{import_name}' not found — installing via pip ...")
    # attempt 1: normal install
    ret = subprocess.run(
        [PYTHON, "-m", "pip", "install", "--quiet", pip_name]
    ).returncode
    # attempt 2: wheels only (no source build => no pg_config/compiler needed)
    if ret != 0:
        warn(f"Source install of '{pip_name}' failed — retrying with "
             "--only-binary :all: (prebuilt wheel only) ...")
        ret = subprocess.run(
            [PYTHON, "-m", "pip", "install", "--quiet",
             "--only-binary", ":all:", pip_name]
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
_PIP_UPGRADED = False


def ensure_pip_current():
    """Make sure the interpreter's pip can see brand-new wheels.

    Fresh Python releases (e.g. 3.14 on Windows) often ship an old bundled
    pip that reports "No matching distribution found" for perfectly valid
    packages simply because it can't parse the latest wheel metadata or the
    package didn't exist yet when that pip was released. Upgrading pip once
    per runner session fixes asyncpg/psycopg2-binary install failures.
    """
    global _PIP_UPGRADED
    if _PIP_UPGRADED:
        return
    _PIP_UPGRADED = True
    ret = subprocess.run([PYTHON, "-m", "pip", "install", "--quiet",
                          "--upgrade", "pip"]).returncode
    if ret == 0:
        ok("pip is up to date (needed for newest wheels on this Python).")
    else:
        warn("Could not upgrade pip — continuing with the bundled version.")


def check_python_modules(with_db=True):
    global HAS_PSUTIL, HAS_PSYCOPG2
    head("STEP 0 — Checking required Python modules")
    ensure_pip_current()
    HAS_PSUTIL = ensure_module("psutil")
    if with_db:
        HAS_PSYCOPG2 = ensure_module("psycopg2", "psycopg2-binary")


# ----------------------------------------------------------------------------
# OS detection & privileged command execution (for auto-installing Node/PG)
# ----------------------------------------------------------------------------
def refresh_path_cache():
    """Re-read PATH from the Windows registry after an installer changed it."""
    if not IS_WINDOWS:
        return
    try:
        import winreg
        for hive, key in ((winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, "Environment")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    val, _ = winreg.QueryValueEx(k, "Path")
                os.environ["PATH"] = val + os.pathsep + \
                    os.environ.get("PATH", "")
            except OSError:
                continue
        # also make sure standard Node install dir is present
        for cand in (r"C:\Program Files\nodejs",):
            if Path(cand).is_dir() and cand not in os.environ.get("PATH", ""):
                add_to_path_front(cand)
    except Exception:
        pass


def run_as_admin(cmd):
    """Run a command elevated on Windows (UAC prompt), or normally elsewhere.

    Returns the process exit code, or 1 on failure to launch.
    """
    if isinstance(cmd, str):
        cmdline = cmd
    else:
        cmdline = subprocess.list2cmdline([str(c) for c in cmd])
    if not IS_WINDOWS:
        prefix = sudo_prefix() or []
        try:
            return subprocess.run(list(prefix) +
                                  (cmd if isinstance(cmd, list) else [cmd])).returncode
        except Exception:
            return 1
    try:
        shell_cmd = f'setlocal & powershell -NoProfile -Command "{cmdline}"'
        ret = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f'$p = Start-Process -FilePath "cmd.exe" '
             f'-ArgumentList "/c {cmdline} ^& exit $LASTEXITCODE" '
             f"-Verb RunAs -Wait -PassThru; exit $p.ExitCode"],
            capture_output=True, text=True).returncode
        return ret
    except Exception:
        return 1


def find_pg_services_windows():
    """Return names of installed-but-stopped PostgreSQL Windows services."""
    svcs = []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Service | Where-Object {$_.Name -like 'postgres*'} | "
             "Select-Object -ExpandProperty Name"],
            capture_output=True, text=True).stdout
        svcs = [s.strip() for s in out.splitlines() if s.strip()]
    except Exception:
        pass
    return svcs


def wait_for_pg_service_ready(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pg_is_running():
            return True
        time.sleep(2)
    return False


# NOTE: IS_WINDOWS is defined HERE, before every function that uses it.
# It used to be defined further down the file; on Windows that made
# ensure_postgres() raise NameError('IS_WINDOWS'), which was swallowed and
# mis-reported as "Could not install PostgreSQL via apt/brew". Fixed.
IS_WINDOWS = (os.name == "nt")


def detect_os():
    """Return 'windows', 'debian', 'macos', or 'other'."""
    if IS_WINDOWS:
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("ID_LIKE=") and ("debian" in line or "ubuntu" in line):
                return "debian"
            if line.startswith("ID=") and ("debian" in line or "ubuntu" in line):
                return "debian"
    except Exception:
        pass
    return "other"


def pg_env_password():
    """Superuser password entered by the user during the Windows installer."""
    return os.environ.get("PGPASS") or ""


def winget_install(pkg_id):
    """Install via winget with agreements auto-accepted. Returns True on success."""
    exe = shutil.which("winget")
    if not exe:
        return False
    ret = subprocess.run([exe, "install", "-e", "--id", pkg_id, "--accept-source-agreements",
                          "--accept-package-agreements"]).returncode
    return ret == 0


def choco_install(pkg_id):
    exe = shutil.which("choco")
    if not exe:
        return False
    return subprocess.run([exe, "install", "-y", pkg_id]).returncode == 0


def download_file(url, dest, quiet=False):
    import urllib.request

    class _UA(urllib.request.Request):
        def add_header(self, key, val):   # urllib blocks User-Agent otherwise
            super().add_header(key.replace("-", "_"), val)

    req = _UA(url)
    req.add_header("User-Agent", "VidyaJyoti-runner/1.0")
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as fh:
        shutil.copyfileobj(r, fh)
    return dest


def install_node_windows():
    """Windows Node.js install: try winget / choco, then official .msi."""
    info("Installing Node.js LTS on Windows ...")
    if winget_install("OpenJS.NodeJS.LTS"):
        refresh_path_cache()
        if shutil.which("node"):
            ok("Node.js installed via winget.")
            return True
    if choco_install("nodejs-lts"):
        refresh_path_cache()
        if shutil.which("node"):
            ok("Node.js installed via Chocolatey.")
            return True
    info("Package managers unavailable/failed — downloading the official "
         "Node.js LTS .msi installer ...")
    try:
        import urllib.request
        index = json.loads(urllib.request.urlopen(
            "https://nodejs.org/dist/index.json", timeout=30).read())
        latest_lts = next(v for v in index if v.get("lts"))
        ver = latest_lts["version"]                      # e.g. 'v22.14.0'
        arch = "x64" if platform.machine() in ("AMD64", "x86_64") else "arm64"
        fname = f"node-{ver}-win-{arch}.msi"
        url = f"https://nodejs.org/dist/{ver}/{fname}"
        tmp = Path(tempfile.gettempdir()) / fname
        download_file(url, tmp)
        info(f"Running installer for {fname} — please complete the Node.js "
             "setup wizard (keep 'Add to PATH' checked) ...")
        subprocess.run(["msiexec", "/i", str(tmp)])
        tmp.unlink(missing_ok=True)
        refresh_path_cache()
        if shutil.which("node"):
            ok("Node.js installed via official MSI.")
            return True
        err("MSI ran but node is still not on PATH. Open a NEW terminal "
            "(or reboot) and re-run: python runner.py")
        return False
    except Exception as e:
        err(f"Windows Node.js install failed: {e}")
        print(f"   {Y}Manual option: install Node LTS from https://nodejs.org "
              f"and re-run python runner.py{X}")
        return False


def install_postgres_windows(auto_install=True):
    """Make PostgreSQL reachable on Windows.

    Strategy (in order):
      1. Start an already-installed Windows service (postgresql-x64-*).
      2. winget / Chocolatey install of PostgreSQL 16 (silent, unattended —
         superuser password becomes 'postgres').
      3. Download & run the EnterpriseDB interactive installer (the runner
         remembers the password you enter via env var PGPASS for STEP 3).
      4. Docker fallback (if docker + WSL2 are present).
    """
    # 1) existing-but-stopped service?
    for svc in find_pg_services_windows():
        info(f"Trying to start Windows service '{svc}' ...")
        if run_as_admin(["sc", "start", svc]) == 0:
            for _ in range(20):
                if pg_is_running():
                    ok(f"PostgreSQL service '{svc}' started.")
                    return True
            warn(f"Service '{svc}' did not come up within 20s.")
    if not auto_install:
        return False

    # 2) winget / choco (fully silent)
    pw = "postgres"
    if winget_install("PostgreSQL.PostgreSQL.16"):
        refresh_path_cache()
        if wait_for_pg_service_ready():
            ok("PostgreSQL 16 installed via winget.")
            os.environ["PGPASS"] = pw
            return True
    if choco_install("postgresql"):
        refresh_path_cache()
        if wait_for_pg_service_ready():
            ok("PostgreSQL installed via Chocolatey.")
            os.environ["PGPASS"] = pw
            return True

    # 3) EnterpriseDB installer (interactive GUI)
    info("No package manager succeeded — downloading the official "
         "PostgreSQL 16 installer (EnterpriseDB) ...")
    try:
        edb_base = ("https://get.enterprisedb.com/postgresql/"
                    "postgresql-16.4-2-windows-x64.exe")
        tmp = Path(tempfile.gettempdir()) / "vj-postgresql-setup.exe"
        download_file(edb_base, tmp)
        print(f"{Y}An installer wizard will open. IMPORTANT:{X}\n"
              f"  • Database superuser password: enter  {B}postgres{X}\n"
              f"    (this runner will use it automatically; keep defaults "
              f"for everything else)\n"
              f"  • When it finishes, this script continues on its own.")
        os.environ["PGPASS"] = pw
        subprocess.run([str(tmp), "--mode", "wizard"])
        tmp.unlink(missing_ok=True)
        refresh_path_cache()
        if wait_for_pg_service_ready():
            ok("PostgreSQL installed via EnterpriseDB installer.")
            return True
    except Exception as e:
        warn(f"EnterpriseDB installer step failed: {e}")

    # 4) docker fallback
    if start_pg_docker():
        return True
    err("Automatic PostgreSQL installation failed on Windows.")
    print(f"   {Y}Install it manually (see README.md STEP 1 — Windows section)"
          f" and re-run python runner.py{X}")
    return False


def sudo_prefix():
    """'' when running as root, else ['sudo'] (sudo is verified/installed)."""
    if IS_WINDOWS:
        return []          # elevation handled by run_as_admin instead
    if os.geteuid() == 0 if hasattr(os, "geteuid") else False:
        return []
    if shutil.which("sudo"):
        return ["sudo"]
    # not root and no sudo available
    return None


def run_privileged(cmd, **kw):
    """Run a command with sudo when needed. Returns subprocess return code."""
    prefix = sudo_prefix()
    if prefix is None:
        err("Need root/sudo to install system packages, but neither is available.")
        return 1
    env = dict(kw.pop("env", None) or os.environ, DEBIAN_FRONTEND="noninteractive")
    ret = subprocess.run(prefix + cmd, env=env, **kw).returncode
    if ret != 0 and prefix:
        warn(f"Command failed{'' if kw.get('capture_output') else ''}: "
             f"{' '.join(cmd)}\n      If this needs your password, re-run: "
             f"sudo {' '.join(cmd)}")
    return ret


NODE_TARBALL_HOME = Path.home() / ".local" / "node"


def add_to_path_front(directory):
    os.environ["PATH"] = f"{directory}{os.pathsep}" + os.environ.get("PATH", "")


def persist_path_hint(directory):
    """Append PATH export to shell rc files so future shells find the tools."""
    line = f'export PATH="{directory}:$PATH"  # added by VidyaJyoti runner.py'
    for rc in (".bashrc", ".zshrc", ".profile"):
        try:
            rc_path = Path.home() / rc
            if rc_path.exists() and line in rc_path.read_text():
                continue
            with open(rc_path, "a") as f:
                f.write("\n" + line + "\n")
        except Exception:
            pass


def install_node_tarball():
    """Last-resort Node install: official tarball into ~/.local/node."""
    import urllib.request
    import tarfile
    info("Installing Node.js LTS from the official tarball into "
         f"{NODE_TARBALL_HOME} ...")
    try:
        arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64",
                "arm64": "arm64"}[os.uname().machine]
        plat = {"Linux": "linux", "Darwin": "darwin"}[sys.platform.capitalize()
                                                      if sys.platform != "darwin"
                                                      else "Darwin"]
        index = json.loads(urllib.request.urlopen(
            "https://nodejs.org/dist/index.json", timeout=30).read())
        latest_lts = next(v for v in index if v.get("lts"))
        ver = latest_lts["version"]
        fname = f"node-{ver}-{plat}-{arch}.tar.xz"
        url = f"https://nodejs.org/dist/{ver}/{fname}"
        NODE_TARBALL_HOME.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(f"/tmp/{fname}")
        urllib.request.urlretrieve(url, tmp)
        with tarfile.open(tmp) as tf:
            tf.extractall(NODE_TARBALL_HOME.parent)
        extracted = NODE_TARBALL_HOME.parent / f"node-{ver}-{plat}-{arch}"
        if NODE_TARBALL_HOME.exists():
            shutil.rmtree(NODE_TARBALL_HOME)
        extracted.rename(NODE_TARBALL_HOME)
        tmp.unlink(missing_ok=True)
        bin_dir = NODE_TARBALL_HOME / "bin"
        add_to_path_front(str(bin_dir))
        persist_path_hint(str(bin_dir))
        ok(f"Node.js {ver} installed to {NODE_TARBALL_HOME}.")
        return True
    except Exception as e:
        err(f"Tarball Node install failed: {e}")
        return False


def ensure_node(auto_install=True):
    """Guarantee node+npm on PATH; install Node LTS if missing."""
    if IS_WINDOWS:
        refresh_path_cache()          # picks up installs from other terminals
    if shutil.which("node") and (shutil.which("npm") or shutil.which("npm.cmd")):
        return True
    if not auto_install:
        err("Node.js/npm not found and --no-install was given.")
        return False
    warn("Node.js / npm NOT found — auto-installing now (this may take a few minutes) ...")
    ostype = detect_os()
    if ostype == "windows":
        return install_node_windows()
    brew = shutil.which("brew")
    if ostype == "debian":
        if run_privileged(["apt-get", "update"]) == 0 and \
           run_privileged(["apt-get", "install", "-y", "curl", "ca-certificates"]) == 0:
            script = ("/usr/local/share/nodesource_setup.sh")
            try:
                import urllib.request
                urllib.request.urlretrieve(
                    "https://deb.nodesource.com/setup_22.x", script)
                if run_privileged(["bash", script]) == 0 and \
                   run_privileged(["apt-get", "install", "-y", "nodejs"]) == 0:
                    ok("Node.js installed via apt/NodeSource.")
            except Exception as e:
                warn(f"NodeSource install failed: {e}")
    elif ostype == "macos" and brew:
        if run_privileged([brew, "install", "node"]) == 0:
            ok("Node.js installed via Homebrew.")
    if not (shutil.which("node") and shutil.which("npm")):
        if not install_node_tarball():
            print(f"   {Y}Manual option: install Node LTS from https://nodejs.org "
                  f"and re-run python runner.py{X}")
            return False
    return bool(shutil.which("node") and shutil.which("npm"))


# ----------------------------------------------------------------------------
# PostgreSQL: locate server binaries, auto-install, start server
# ----------------------------------------------------------------------------
def pg_bin_candidates():
    """Directories that may contain postgres/initdb/psql binaries."""
    dirs = [Path("/usr/lib/postgresql")]
    found = []
    for d in dirs:
        if d.is_dir():
            for v in sorted(d.iterdir(), reverse=True):
                found.append(v / "bin")
    found += [Path("/usr/bin"), Path("/usr/local/bin"), Path("/opt/homebrew/bin"),
              Path("/Library/PostgreSQL")]
    extra = Path("/opt/homebrew/opt")
    if extra.is_dir():
        for p in sorted(extra.glob("postgresql*"), reverse=True):
            found.append(p / "bin")
    win = Path("C:/Program Files/PostgreSQL")
    if win.is_dir():
        for v in sorted(win.iterdir(), reverse=True):
            found.append(v)
    return found


def find_pg_binary(name):
    direct = shutil.which(name)
    if direct:
        return Path(direct)
    for d in pg_bin_candidates():
        cand = d / name
        if cand.exists():
            return cand
        for sub in (d.glob(f"*/{name}") if d.is_dir() else []):
            return sub
    return None


def pg_is_running(port=DB_PORT):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", port)) == 0


PG_DATA_DIR = ROOT / ".vj_pgdata"


def start_local_pg_server():
    """Start an embedded PostgreSQL server using data dir ./.vj_pgdata."""
    initdb, pg_ctl = find_pg_binary("initdb"), find_pg_binary("pg_ctl")
    if not initdb or not pg_ctl:
        return False
    if not PG_DATA_DIR.exists():
        info(f"Initializing a local PostgreSQL cluster in "
             f"{rel(PG_DATA_DIR)} ...")
        PG_DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            PG_DATA_DIR.chmod(0o750)
        except Exception:
            pass
        # initdb refuses to run as root -> use the 'postgres' OS user if present
        run_as_root = hasattr(os, "geteuid") and os.geteuid() == 0
        if run_as_root and any(u.split(":")[0] == "postgres"
                               for u in Path("/etc/passwd").read_text().splitlines()):
            cmd = ["su", "postgres", "-c",
                   f"{initdb} -D {PG_DATA_DIR} -A trust -U postgres"]
        else:
            if run_as_root:
                warn("Running as root without a 'postgres' OS user — initdb "
                     "may refuse. Trying anyway ...")
            cmd = [str(initdb), "-D", str(PG_DATA_DIR), "-A", "trust", "-U", "postgres"]
        if subprocess.run(cmd).returncode != 0:
            err("initdb failed.")
            return False
    info("Starting local PostgreSQL server on port "
         f"{DB_PORT} (logs: .vj_logs/postgres.log) ...")
    LOG_DIR.mkdir(exist_ok=True)
    cmd = [str(pg_ctl), "-D", str(PG_DATA_DIR), "-l",
           str(LOG_DIR / "postgres.log"), "-w", "start"]
    if hasattr(os, "geteuid") and os.geteuid() == 0 and \
       "postgres" in Path("/etc/passwd").read_text():
        cmd = ["su", "postgres", "-c", " ".join(cmd)]
    subprocess.run(cmd)
    for _ in range(20):
        if pg_is_running():
            ok("Local PostgreSQL server is UP.")
            return True
        time.sleep(1)
    err("Local PostgreSQL server did not start. Check .vj_logs/postgres.log")
    return False


def start_pg_docker():
    """Fallback: run the official postgres:16 image in Docker."""
    if not shutil.which("docker"):
        return False
    name = "vj-pg"
    r = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"],
                       capture_output=True, text=True)
    if name in r.stdout.split():
        subprocess.run(["docker", "start", name])
        ok(f"Reused existing Docker container '{name}'.")
    else:
        info("No package manager path available — starting PostgreSQL via "
             "Docker (postgres:16) ...")
        ret = subprocess.run([
            "docker", "run", "-d", "--name", name,
            "-p", f"{DB_PORT}:5432",
            "-e", "POSTGRES_PASSWORD=postgres",
            "postgres:16"]).returncode
        if ret != 0:
            return False
    for _ in range(30):
        if pg_is_running():
            ok("PostgreSQL (Docker) is UP on port "
               f"{DB_PORT}.")
            return True
        time.sleep(1)
    return False


def pg_trust_local_auth():
    """Allow loopback TCP connections with 'trust' auth (only on 127.0.0.1).

    Needed when we cannot log in as the postgres superuser at all (fresh
    install with scram-sha-256 host rules). Safe for local development;
    VidyaJyoti still uses its own vj_user/vj_password role afterwards.

    NOTE: pg_hba rules are matched FIRST-match-wins, so our trust lines must
    be PREPENDED before the existing scram-sha-256 host lines.
    """
    hba = None
    for cand in sorted(Path("/etc/postgresql").glob("*/*/pg_hba.conf")):
        hba = cand
        break
    if hba is None:
        return False
    text = hba.read_text()
    marker = "# added by VidyaJyoti runner.py (local dev trust auth)"
    trust_lines = ["host all all 127.0.0.1/32 trust",
                   "host all all ::1/128 trust"]
    lines = text.splitlines(keepends=True)
    # A previous version of this script appended the trust lines at the END,
    # where they are shadowed by earlier scram rules. Remove any copies and
    # re-insert them before the first active rule (first-match-wins).
    cleaned = [ln for ln in lines
               if ln.strip() != marker and ln.strip() not in trust_lines]
    insert_at = 0
    for i, ln in enumerate(cleaned):
        s = ln.strip()
        if s and not s.startswith("#"):
            insert_at = i
            break
    block = f"{marker}\n" + "\n".join(trust_lines) + "\n"
    cleaned.insert(insert_at, block)
    try:
        tmp = hba.with_suffix(".conf.vjtmp")
        tmp.write_text("".join(cleaned))
        shutil.move(str(tmp), str(hba))
        ok("Ensured password-less 'trust' auth for localhost only "
           "(pg_hba.conf) so bootstrap can run.")
    except Exception as e:
        warn(f"Could not edit pg_hba.conf: {e}")
        return False
    # reload (idempotent) so the new rules take effect immediately
    ver, cl = hba.parent.parent.name, hba.parent.name
    reloaded = False
    if shutil.which("pg_ctlcluster"):
        reloaded = run_privileged(["pg_ctlcluster", ver, cl, "reload"]) == 0
    svc = shutil.which("service")
    if not reloaded and svc:
        reloaded = run_privileged([svc, "postgresql", "reload"]) == 0
    if not reloaded:
        psql = find_pg_binary("psql")
        if psql:
            subprocess.run(["su", "postgres", "-c", f"{psql} -c 'SELECT pg_reload_conf()'"],
                           capture_output=True)
    time.sleep(2)
    return True


def ensure_postgres(auto_install=True):
    """Make sure a reachable PostgreSQL exists; install+start one if needed."""
    if pg_is_running():
        ok(f"PostgreSQL already reachable at localhost:{DB_PORT}.")
        return True
    if IS_WINDOWS:
        return install_postgres_windows(auto_install=auto_install)
    if not auto_install:
        warn("PostgreSQL not reachable and --no-install was given; "
             "DB-backed endpoints will fail.")
        return False
    warn(f"No PostgreSQL server reachable at localhost:{DB_PORT} — "
         "auto-installing now ...")
    ostype = detect_os()
    brew = shutil.which("brew")
    pkg_installed = False
    if ostype == "debian":
        pkgs = ["postgresql", "postgresql-contrib"]
        if run_privileged(["apt-get", "update"]) == 0 and \
           run_privileged(["apt-get", "install", "-y"] + pkgs) == 0:
            pkg_installed = True
    elif ostype == "macos" and brew:
        if run_privileged([brew, "install", "postgresql@16"]) == 0:
            pkg_installed = True
            add_to_path_front("/opt/homebrew/opt/postgresql@16/bin")
    if pkg_installed:
        ok("PostgreSQL installed via system package manager.")
    elif find_pg_binary("initdb"):
        info("PostgreSQL binaries already present but server stopped.")
    else:
        # Show the REAL reason (missing package manager / no root / etc.)
        # instead of the old blanket "apt/brew" message that hid errors.
        reasons = []
        if ostype == "debian":
            reasons.append("apt-get update/install failed "
                           "(no internet or no sudo rights?)")
        elif ostype == "macos":
            reasons.append("Homebrew not found or 'brew install postgresql@16' failed")
        else:
            reasons.append(f"no supported package manager for OS '{ostype}'")
        warn("Could not install PostgreSQL via system package manager: "
             + "; ".join(reasons) + " — trying Docker ...")
        if start_pg_docker():
            return True
        err("Automatic PostgreSQL installation failed.")
        print(f"   {Y}Install it manually (see README.md STEP 1) and re-run "
              f"python runner.py{X}")
        return False
    # Start the server: prefer the OS service, fall back to embedded cluster
    svc = shutil.which("service")
    if svc and ostype == "debian":
        run_privileged([svc, "postgresql", "start"])
    pg_ctl = find_pg_binary("pg_ctl")
    if pg_ctl and not pg_is_running():
        # default Debian cluster?
        clusters = Path("/etc/postgresql")
        if clusters.is_dir():
            for ver in sorted(clusters.iterdir(), reverse=True):
                for cl in sorted(ver.iterdir(), reverse=True):
                    conf = cl / "postgresql.conf"
                    if conf.exists() and "port = 5433" in conf.read_text():
                        continue
                    subprocess.run(
                        ["su", "postgres", "-c",
                         f"{pg_ctl} -D {cl}/data -w start"]
                        if hasattr(os, "geteuid") and os.geteuid() == 0
                        else [str(pg_ctl), "-D", str(cl / "data"), "-w", "start"])
                    if pg_is_running():
                        break
    if not pg_is_running():
        if not start_local_pg_server():
            return start_pg_docker()
    return pg_is_running()


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
def check_node_and_frontend(auto_install=True):
    head("STEP 1 — Node.js & frontend dependencies")
    if not ensure_node(auto_install=auto_install):
        sys.exit(1)
    ok(f"Node.js found: {subprocess.run(['node', '-v'], capture_output=True, text=True).stdout.strip()}")

    if not FRONTEND_DIR.exists():
        err(f"Frontend folder not found at {FRONTEND_DIR}")
        sys.exit(1)

    if not (FRONTEND_DIR / "node_modules").exists():
        info("node_modules missing — running 'npm install' (this can take a while) ...")
        ret = subprocess.run([npm_cmd(), "install", "--no-audit", "--no-fund"],
                             cwd=FRONTEND_DIR, shell=IS_WINDOWS).returncode
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


# Pinned versions that have NO prebuilt wheel for some platforms / Python
# combos (e.g. asyncpg==0.30.0 has no Windows wheels; psycopg2-binary can also
# fail on brand-new Python releases). If the pinned install fails, we retry
# with these flexible specs so the app installs on ANY machine.
FALLBACK_PACKAGES = {
    "asyncpg": "asyncpg>=0.29.0",
    "psycopg2-binary": "psycopg2-binary>=2.9.9",
    "faiss-cpu": "faiss-cpu>=1.8.0",
    "numpy": "numpy>=1.26.0,<3",
    "pandas": "pandas>=2.2.0",
    "torch": "torch>=2.3.0",
}


def pip_install_py_files(workdir):
    """Find *.py files in workdir (excluding dot-dirs like .venv)."""
    try:
        return [p for p in workdir.iterdir()
                if p.is_file() and p.suffix == ".py"]
    except Exception:
        return []


def write_marker(requirements_path, marker_path):
    try:
        mtime = os.path.getmtime(requirements_path)
        with open(marker_path, "w") as fh:
            fh.write(str(mtime))
    except Exception:
        pass


def install_requirements(name, workdir, marker_pkg, optional=False):
    """Create .venv inside workdir (if needed) and pip install requirements.txt.

    Robustness features:
      • Never wipes an existing working venv (safe to re-run).
      • Auto-retries WITHOUT version pins if a package has no wheel
        for this OS / Python version (fixes 'asyncpg==0.30.0 — No matching
        distribution found' on Windows).
      • Only reinstalls when requirements.txt changed or something is missing.
      • optional=True (ML service): if even core install fails, do NOT abort —
        print instructions and return None so the runner continues without it.
    """
    head(f"STEP 2 — Installing '{name}' Python dependencies")
    venv_dir = workdir / ".venv"
    py, _ = venv_pip_and_python(venv_dir)
    if not py.exists():
        info(f"Creating virtual environment at {rel(venv_dir)} ...")
        ret = subprocess.run([PYTHON, "-m", "venv", str(venv_dir)]).returncode
        if ret != 0:
            err("Failed to create venv.")
            sys.exit(1)

    req = workdir / "requirements.txt"
    marker = venv_dir / ".vj_req_hash"

    # Stale-venv guard: if .venv was created by a DIFFERENT (older) Python
    # than the one running the runner now, its pip cache can be too old to see
    # new wheels — nuke and recreate so everything lands on the current Python.
    ver_marker = venv_dir / ".vj_pyver"
    want_ver = f"{sys.version_info[0]}.{sys.version_info[1]}"
    try:
        prev_ver = ver_marker.read_text().strip()
    except Exception:
        prev_ver = ""
    if py.exists() and prev_ver and prev_ver != want_ver:
        warn(f"Existing .venv was built with Python {prev_ver}, but you are now "
             f"running Python {want_ver} — recreating the virtual environment ...")
        shutil.rmtree(venv_dir, ignore_errors=True)
        ret = subprocess.run([PYTHON, "-m", "venv", str(venv_dir)]).returncode
        if ret != 0:
            err("Failed to recreate venv.")
            sys.exit(1)

    # Fresh venvs ship with an old bundled pip that may not know about the
    # newest wheels ("No matching distribution found"). Always upgrade first.
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade",
                    "pip", "setuptools", "wheel", "--quiet"])
    try:
        ver_marker.write_text(want_ver)
    except Exception:
        pass

    def req_changed():
        try:
            prev = open(marker).read().strip()
        except Exception:
            return True
        return prev != str(os.path.getmtime(req))

    check = subprocess.run([str(py), "-c", f"import {marker_pkg}"],
                           capture_output=True)
    if check.returncode == 0 and not req_changed():
        ok(f"'{name}' dependencies already installed.")
        return py

    # --- attempt 1: requirements.txt ----------------------------------------
    if req.exists():
        info(f"pip installing {rel(req)} into {venv_dir.name} "
             "(be patient — first time may download a lot) ...")
        ret = subprocess.run([str(py), "-m", "pip", "install",
                              "--prefer-binary", "-r", str(req)]).returncode
        if ret == 0:
            ok(f"'{name}' dependencies installed.")
            write_marker(req, marker)
            return py
        warn("requirements.txt install failed (a package likely has no wheel "
             "for this OS / Python version, or tried to build from source and "
             "needs tools like pg_config / a C compiler). Retrying with "
             "prebuilt wheels only — this keeps everything compatible...")
        if IS_WINDOWS and sys.version_info >= (3, 13):
            info("HINT: on Windows + Python 3.13/3.14 some packages have no "
                 "wheels yet. If retries below also fail, install Python 3.12 "
                 "(python.org) and run:  py -3.12 runner.py")

    # --- attempt 2: wheels-only install (no source builds => no compilers,   )
    #             no pg_config; pip picks the newest version WITH a wheel     )
    if req.exists():
        ret = subprocess.run([str(py), "-m", "pip", "install",
                              "--only-binary", ":all:",
                              "-r", str(req)]).returncode
        if ret == 0:
            ok(f"'{name}' dependencies installed (wheels only).")
            write_marker(req, marker)
            return py
        warn("Wheel-only install of the full requirements.txt still failed. "
             "Retrying with flexible version ranges package-by-package ...")

    # --- attempt 3: unpinned / fallback versions ----------------------------
    def pkg_name(spec):
        return re.split(r"[=<>!;\[ ]", spec, maxsplit=1)[0].strip()

    specs = []
    if req.exists():
        for line in open(req, encoding="utf-8"):
            line = line.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            base = pkg_name(line)
            # 'base' strips the version spec; map known problem packages to
            # tested-flexible specs, otherwise install fully unpinned.
            specs.append(FALLBACK_PACKAGES.get(base, base))
    else:
        specs = ["fastapi", "uvicorn[standard]", "sqlalchemy[asyncio]",
                 "asyncpg", "pydantic", "pydantic-settings",
                 "psycopg2-binary"]

    info(f"pip installing ({', '.join(specs[:6])} ...) into {venv_dir.name}")
    ret = subprocess.run([str(py), "-m", "pip", "install",
                          "--prefer-binary", *specs]).returncode
    if ret != 0:
        # per-package pass: collect names that genuinely fail on this machine
        failed, kept = [], []
        for s in specs:
            r = subprocess.run([str(py), "-m", "pip", "install",
                                "--prefer-binary", s],
                               capture_output=True, text=True)
            if r.returncode != 0:
                # last resort for this package: prebuilt wheel only
                r = subprocess.run([str(py), "-m", "pip", "install",
                                    "--only-binary", ":all:", s],
                                   capture_output=True, text=True)
            (kept if r.returncode == 0 else failed).append(s)
        if failed:
            warn("Some optional packages have no wheel for this system and "
                 f"were skipped: {', '.join(failed)}")
        core_needed = {"fastapi", "uvicorn", "sqlalchemy", "asyncpg",
                       "pydantic", "pydantic-settings"}
        kept_names = {pkg_name(s) for s in kept}
        still_missing = [c for c in core_needed if c not in kept_names]
        if still_missing:
            if optional:
                warn("Could not fully install ML service dependencies on this "
                     "machine (" + ", ".join(sorted(still_missing)) + ").")
                warn("Continuing WITHOUT the ML service — the app is fully "
                     "usable (backend :8000 + frontend :3000).")
                warn(f"To install it manually later:  {py} -m pip install -r "
                     f"{req}")
                return None
            err("Automatic dependency installation failed for CORE packages: "
                + ", ".join(sorted(still_missing)))
            err("Please run manually:  " + str(py) + " -m pip install -r "
                + str(req))
            sys.exit(1)
    ok(f"'{name}' dependencies installed.")
    if req.exists():
        write_marker(req, marker)
    return py


# ----------------------------------------------------------------------------
# STEP 3 — Bootstrap PostgreSQL (create user + db if missing)
# ----------------------------------------------------------------------------
def bootstrap_postgres(auto_install=True):
    head("STEP 3 — PostgreSQL bootstrap")
    if not pg_is_running():
        warn(f"No PostgreSQL server reachable at {DB_HOST}:{DB_PORT}.")
        print(textwrap_dedent(f'''
             {Y}The app will still start, but DB-backed endpoints will fail.
             Start PostgreSQL first, e.g.:
               • macOS (Homebrew):  brew services start postgresql@16
               • Ubuntu/Debian   :  sudo service postgresql start
               • Docker          :  docker run -d --name vj-pg \
-P 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
               • Windows         :  press Win+R → services.msc → start
                 "postgresql-x64-16"  (or re-run this script WITHOUT --no-install
                 so it auto-installs PostgreSQL for you)
             Then re-run: python runner.py{X}
        '''))
        return False
    ok(f"PostgreSQL reachable at {DB_HOST}:{DB_PORT}.")

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
                  ("postgres", pg_env_password())]
    if IS_WINDOWS:
        # EnterpriseDB installer sets this env var while its wizard runs;
        # it usually holds the superuser password you typed.
        candidates.append(("postgres", os.environ.get("PGPASSWORD", "")))
    candidates.append((os.getenv("USER", "postgres"), ""))
    seen = set()
    candidates = [c for c in candidates
                  if not (c in seen or seen.add(c))]
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
    if admin_conn is None and auto_install:
        # Fresh apt install uses scram-sha-256 for TCP; we can't log in yet.
        # Enable localhost 'trust' auth (dev-only), reload, and retry once.
        info("No superuser login worked — enabling temporary localhost "
             "'trust' auth so bootstrap can proceed ...")
        if pg_trust_local_auth():
            for u, p in [("postgres", ""), (DB_USER, DB_PASS)]:
                try:
                    admin_conn = connect("postgres", u, p)
                    ok(f"Connected to PostgreSQL as '{u}' after trust-auth.")
                    break
                except Exception:
                    continue
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


def npm_cmd():
    """Resolve the npm executable on Windows ('npm' alone is a .cmd shim)."""
    if IS_WINDOWS:
        found = (shutil.which("npm.cmd") or shutil.which("npm.exe")
                 or shutil.which("npm"))
        if not found:
            for cand in (Path(r"C:\Program Files\nodejs\npm.cmd"),
                         Path(os.environ.get("APPDATA", "")) / "npm.cmd"):
                if cand.exists():
                    found = str(cand)
                    break
        return found or "npm"
    return "npm"


def resolve_npm():
    """Return (exe, is_shim) for npm on this machine.

    On Windows 'npm' is really npm.cmd — a batch shim that CreateProcess
    cannot launch directly (FileNotFoundError / WinError 2). We detect the
    real path so we can quote it and/or run it through cmd.exe.
    """
    if not IS_WINDOWS:
        return "npm", False
    found = (shutil.which("npm.cmd") or shutil.which("npm.exe")
             or shutil.which("npm"))
    if not found:
        for cand in (Path(r"C:\Program Files\nodejs\npm.cmd"),
                     Path(os.environ.get("APPDATA", "")) / "npm.cmd"):
            if cand.exists():
                found = str(cand)
                break
    if not found:
        return "npm", True
    is_shim = found.lower().endswith((".cmd", ".bat")) or \
        not found.lower().endswith(".exe")
    return found, is_shim


def start_service(name, cmd, cwd, port, env=None):
    head(f"STEP 4 — Starting {name}")
    LOG_DIR.mkdir(exist_ok=True)
    logfile = LOG_DIR / f"{name}.log"
    fh = open(logfile, "w")
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    # Make sure freshly-installed Node/PG dirs are visible to children.
    if IS_WINDOWS:
        refresh_path_cache()
        for extra in (r"C:\Program Files\nodejs",):
            if Path(extra).is_dir() and extra not in run_env.get("PATH", ""):
                run_env["PATH"] = extra + os.pathsep + run_env.get("PATH", "")
    info(f"Command : {' '.join(str(c) for c in cmd)}")
    info(f"Workdir : {rel(cwd)}")
    info(f"Logfile : {rel(logfile)}")
    popen_kw = {}
    final_cmd = cmd
    # On Windows, batch-file launchers like npm.cmd cannot be started by
    # CreateProcess directly (WinError 2). Run them through cmd.exe with the
    # shim path quoted — quoting matters because user folders often contain
    # spaces (e.g. "C:\Users\Jane Doe\...").
    if IS_WINDOWS and isinstance(cmd, list) and \
            str(cmd[0]).lower().replace("\\", "/")\
               .split("/")[-1].split(".")[0] in ("npm", "npx", "yarn", "pnpm"):
        exe, _ = resolve_npm()
        final_cmd = ["cmd.exe", "/c", subprocess.list2cmdline([exe] + [str(c) for c in cmd[1:]])]
    try:
        proc = subprocess.Popen(final_cmd, cwd=str(cwd), stdout=fh,
                                stderr=subprocess.STDOUT, env=run_env, **popen_kw)
    except FileNotFoundError as e:
        fh.close()
        err(f"Could not start {name}: {e}")
        print(f"   {Y}The launcher executable was not found on PATH. "
              f"Re-run 'python runner.py' (it will re-check installations).{X}")
        return None
    PROCESSES[name] = (proc, port, logfile)
    if wait_for_port(port, timeout=90):
        ok(f"{name} is UP on port {port}  →  http://localhost:{port}")
    else:
        err(f"{name} did NOT come up on port {port}. Last log lines:")
        try:
            print("\n".join(logfile.read_text().splitlines()[-15:]))
        except Exception:
            pass
    return proc


def start_backend(py_bin):
    # Create tables (idempotent) before serving requests.  Only attempt this
    # when PostgreSQL is actually reachable — otherwise we'd just print a
    # giant asyncpg ConnectionRefused traceback for no reason.
    if pg_is_running():
        info("Ensuring database tables exist (Base.metadata.create_all) ...")
        ret = subprocess.run(
            [str(py_bin), "-c",
             "import asyncio, app.models, app.database as d;"
             "asyncio.run(d.init_db()); print('tables ready')"],
            cwd=str(BACKEND_DIR),
            capture_output=True, text=True).returncode
        if ret == 0:
            ok("Database tables are ready.")
        else:
            warn("Could not pre-create tables — backend will still start; "
                 "DB endpoints may fail until the schema exists.")
    else:
        warn("PostgreSQL is not reachable — skipping table creation. "
             "DB-backed endpoints will fail until you start the database "
             "(see README.md STEP 1), then re-run python runner.py.")
    start_service(
        "backend",
        [str(py_bin), "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
        BACKEND_DIR, BACKEND_PORT,
    )


def start_frontend():
    """Launch 'npm run dev' on Windows in a CreateProcess-safe way.

    On Windows, 'npm' is really npm.cmd — a batch shim that Python's
    CreateProcess cannot launch directly (FileNotFoundError / WinError 2),
    and user folders often contain spaces ("OneDrive\\Desktop"), so quoting
    matters. Strategy:
      1. resolve the real npm.cmd path,
      2. build one fully-quoted cmd.exe command line,
      3. if even that fails to spawn, fall back to running Vite directly
         with Node (node node_modules/vite/bin/vite.js) — no npm needed.
    """
    if IS_WINDOWS:
        refresh_path_cache()
        exe, _ = resolve_npm()
        quoted = subprocess.list2cmdline([exe, "run", "dev"])
        cmd_line = f'@echo off & chcp 65001 >nul & cd /d "{FRONTEND_DIR}" & {quoted}'
        LOG_DIR.mkdir(exist_ok=True)
        logfile = LOG_DIR / "frontend.log"
        head("STEP 4 — Starting frontend")
        info(f"Command : {quoted}")
        info(f"Workdir : {rel(FRONTEND_DIR)}")
        info(f"Logfile : {rel(logfile)}")
        try:
            with open(logfile, "w") as fh:
                proc = subprocess.Popen(
                    ["cmd.exe", "/c", cmd_line],
                    cwd=str(FRONTEND_DIR), stdout=fh,
                    stderr=subprocess.STDOUT)
            PROCESSES["frontend"] = (proc, FRONTEND_PORT, logfile)
            if wait_for_port(FRONTEND_PORT, timeout=90):
                ok(f"frontend is UP on port {FRONTEND_PORT}  →  "
                   f"http://localhost:{FRONTEND_PORT}")
                return proc
            err("frontend did NOT come up via npm.cmd. Last log lines:")
            try:
                print("\n".join(logfile.read_text().splitlines()[-15:]))
            except Exception:
                pass
            warn("Falling back to running Vite directly with Node ...")
        except OSError as e:
            err(f"Could not spawn npm via cmd.exe ({e}).")
            warn("Falling back to running Vite directly with Node ...")
        # ---- fallback: node + vite.js (bypasses npm entirely) --------------
        node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
        vite_js = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
        if Path(node).exists() and vite_js.exists():
            return start_service(
                "frontend",
                [str(node), str(vite_js), "--host", "0.0.0.0",
                 "--port", str(FRONTEND_PORT)],
                FRONTEND_DIR, FRONTEND_PORT)
        err("Frontend could not be started. Ensure Node.js is installed "
            "(python runner.py can install it automatically without --no-install).")
        return None
    return start_service(
        "frontend",
        [npm_cmd(), "run", "dev"],
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
    print(f"\n  Logs folder : {rel(LOG_DIR)}/")
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
                        help="Only install/bootstrap everything & DB, don't start servers.")
    parser.add_argument("--no-install", action="store_true",
                        help="Skip auto-installing Node.js / PostgreSQL (assume present).")
    parser.add_argument("--no-ml", action="store_true",
                        help="Skip the self-hosted ML service (port 9000).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--backend-only", action="store_true")
    group.add_argument("--frontend-only", action="store_true")
    group.add_argument("--ml-only", action="store_true")
    args = parser.parse_args()
    auto_install = not args.no_install

    print(f"\n{B}{C}🪔  VIDYAJYOTI — ONE-CLICK RUNNER{X}{B}"
          f"  (no external AI APIs — fully self-hosted){X}\n")

    # Windows + brand-new Python (e.g. 3.14): some packages such as asyncpg
    # ship NO wheels yet and psycopg2-binary needs pg_config to build from
    # source. Warn early and prefer a supported interpreter if we can find one.
    if IS_WINDOWS and sys.version_info >= (3, 13):
        warn(f"Detected Python {sys.version.split()[0]} on Windows. Some packages "
             "(asyncpg / psycopg2-binary) may not have prebuilt wheels for it yet.")
        for cand in ("py -3.12", "py -3.11", "python3.12", "python3.11"):
            probe = cand.split()
            try:
                r = subprocess.run([probe[0], *(probe[1:]), "-c", "print('ok')"],
                                   capture_output=True, text=True, timeout=15)
            except Exception:
                continue
            if r.returncode == 0 and "ok" in r.stdout:
                print(f"   {Y}Tip: re-run with that interpreter for the smoothest "
                      f"install, e.g.:{X}")
                print(f"   {B}{cand} runner.py{X}")
                break

    # STEP 0
    check_python_modules(with_db=not args.frontend_only)

    # STEP 1 — Node.js (auto-installs npm/Node if missing!) + frontend deps
    check_node_and_frontend(auto_install=auto_install)

    # STEP 2 — python deps
    backend_py = install_requirements("backend", BACKEND_DIR, "fastapi")
    ml_py = None
    if not args.no_ml and not args.backend_only and not args.frontend_only:
        ml_py = install_requirements("ml_service", ML_DIR, "fastapi", optional=True)

    # STEP 3 — PostgreSQL (auto-installs + starts it if missing!) then bootstrap
    if not args.frontend_only and not args.ml_only:
        ensure_postgres(auto_install=auto_install)
        bootstrap_postgres(auto_install=auto_install)

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
