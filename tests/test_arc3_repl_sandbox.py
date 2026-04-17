
import pytest
from agents.arc3.repl_sandbox import execute_repl

def test_repl_basic_calculation():
    res = execute_repl("print(1 + 1)")
    assert res["stdout"].strip() == "2"
    assert res["exit_code"] == 0
    assert res["timeout"] is False

def test_repl_numpy_available():
    # Only if numpy is installed in the test env
    try:
        import numpy
        res = execute_repl("import numpy as np; print(np.array([1,2,3]).sum())")
        if res["stdout"].strip() != "6":
            print(f"DEBUG: stdout='{res['stdout']}' stderr='{res['stderr']}'")
        assert res["stdout"].strip() == "6"
        assert res["exit_code"] == 0
    except ImportError:
        pytest.skip("numpy not available in test environment")

def test_repl_blocked_import():
    res = execute_repl("import subprocess; print(subprocess.run(['ls']))")
    # Should fail because we set sys.modules['subprocess'] = None
    assert res["exit_code"] != 0
    assert "ModuleNotFoundError" in res["stderr"] or "ImportError" in res["stderr"]

def test_repl_timeout():
    res = execute_repl("import time; time.sleep(1)", timeout=0.1)
    assert res["timeout"] is True
    assert "Timed out" in res["stderr"]

def test_repl_syntax_error():
    res = execute_repl("print(invalid code")
    assert res["exit_code"] != 0
    assert "SyntaxError" in res["stderr"]
