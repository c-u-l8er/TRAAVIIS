"""Locate and load the Forge/TRVM engine through its stable public API.

TRAAVIIS carries no world semantics. Every `trvs` command is a thin call into
the versioned `forge_api` boundary of the Forge engine that lives in the TRVM
repository (`TRVM/forge/`). This module finds that engine directory, puts it on
`sys.path`, imports `forge_api`, and checks its `ENGINE_API_VERSION` -- refusing
an incompatible engine cleanly rather than importing something unexpected.

Resolution order for the forge directory:
  1. $TRVS_FORGE_DIR   -- explicit override; if set it MUST be valid (fail fast,
                          never silently fall through to a different engine)
  2. sibling checkout ../TRVM/forge   (ProjectAmp2 monorepo layout)
  3. $PWD/TRVM/forge and $PWD upward   (running from inside a checkout)
"""
import os
import sys

# The engine API version this build of TRAAVIIS was written against.
REQUIRED_ENGINE_API = "1"

_ENGINE = None


def _is_engine_dir(path):
    """A forge dir is usable only if it exposes the public API module."""
    return bool(path) and os.path.isfile(os.path.join(path, "forge_api.py"))


def _die(msg):
    sys.stderr.write("trvs: " + msg + "\n")
    raise SystemExit(2)


def _search_candidates():
    seen = []

    def add(p):
        if p:
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.append(ap)

    # This file: <TRAAVIIS>/traaviis/engine.py -> ProjectAmp2/TRVM/forge
    here = os.path.dirname(os.path.abspath(__file__))
    proj_root = os.path.dirname(os.path.dirname(here))
    add(os.path.join(proj_root, "TRVM", "forge"))
    cur = os.path.abspath(os.getcwd())
    while True:
        add(os.path.join(cur, "TRVM", "forge"))
        add(os.path.join(cur, "forge"))
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return seen


def _resolve_forge_dir():
    override = os.environ.get("TRVS_FORGE_DIR")
    if override:
        # Explicit override: fail fast if invalid; do NOT silently search on.
        if not os.path.isdir(override):
            _die("TRVS_FORGE_DIR is not a directory: %s" % override)
        if not _is_engine_dir(override):
            _die("TRVS_FORGE_DIR has no forge_api.py (not a Forge engine): %s"
                 % override)
        return os.path.abspath(override)
    for c in _search_candidates():
        if _is_engine_dir(c):
            return c
    _die("could not locate the Forge/TRVM engine.\n"
         "  Set TRVS_FORGE_DIR to your TRVM/forge directory, e.g.\n"
         "    export TRVS_FORGE_DIR=/path/to/TRVM/forge")


def load():
    """Return the loaded `forge_api` engine module (memoized).

    Exits (code 2) with a clear message if the engine cannot be found, imported,
    or its API version is incompatible with this build of TRAAVIIS.
    """
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    forge = _resolve_forge_dir()
    if forge not in sys.path:
        sys.path.insert(0, forge)
    try:
        import forge_api
    except Exception as ex:  # engine present but failed to import
        _die("failed to import the Forge engine at %s\n  %s: %s"
             % (forge, type(ex).__name__, ex))
    found = getattr(forge_api, "ENGINE_API_VERSION", None)
    if found != REQUIRED_ENGINE_API:
        _die("incompatible Forge engine\n"
             "  required API   %s\n  found API      %s\n  location       %s"
             % (REQUIRED_ENGINE_API, found, forge))
    _ENGINE = forge_api
    return forge_api
