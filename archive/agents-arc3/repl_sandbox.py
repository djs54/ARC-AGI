"""Restricted Python REPL for ARC grid logic verification — B123."""

import sys
import subprocess
import tempfile
import os
import logging

logger = logging.getLogger(__name__)

def execute_repl(code: str, timeout: float = 2.0) -> dict:
    """
    Executes a short Python snippet in a restricted subprocess.
    B123: Blocks non-whitelisted imports and enforces a strict timeout.
    """
    # B123: Blacklist approach for better library compatibility (numpy needs many submodules)
    # but still blocking core dangerous operations.
    restricted_prelude = (
        "import sys, os\n"
        "def blocked(*args, **kwargs):\n"
        "    print('Action blocked by mental sandbox', file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "os.system = blocked\n"
        "os.spawnv = blocked\n"
        "os.execv = blocked\n"
        "os.popen = blocked\n"
        "sys.modules['subprocess'] = None\n"
        "sys.modules['socket'] = None\n"
        "sys.modules['requests'] = None\n"
        "sys.modules['urllib'] = None\n"
    )
    
    full_code = restricted_prelude + code
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as tmp:
        tmp.write(full_code)
        tmp_path = tmp.name

    try:
        # Use absolute path to venv python if we are in one, else sys.executable
        cwd = os.getcwd()
        venv_python = os.path.join(cwd, ".venv", "bin", "python3")
        python_exe = venv_python if os.path.exists(venv_python) else sys.executable
        
        proc = subprocess.run(
            [python_exe, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "timeout": False
        }
    except subprocess.TimeoutExpired as e:
        # e.stdout/stderr might be bytes if not using text=True in exception catch
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return {
            "stdout": stdout,
            "stderr": stderr or f"Timed out after {timeout}s",
            "exit_code": -1,
            "timeout": True
        }
    except Exception as e:
        logger.error("REPL execution failed: %s", e)
        return {
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
            "timeout": False
        }
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
