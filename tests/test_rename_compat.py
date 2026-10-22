"""Product renamed: legacy WASL_* settings still work; KARESHAFAA_* wins."""
import os
import subprocess
import sys


def _setting(env: dict, name: str) -> str:
    code = f"import kareshafaa; from kareshafaa.config import Settings; print(getattr(Settings(), {name!r}))"
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("WASL_", "KARESHAFAA_"))}
    return subprocess.run([sys.executable, "-c", code], env={**clean, **env}, capture_output=True, text=True,
                          check=True, cwd=os.path.dirname(os.path.dirname(__file__))).stdout.strip()


def test_legacy_wasl_env_is_accepted():
    assert _setting({"WASL_REQUEST_TIMEOUT_S": "7"}, "request_timeout_s") == "7.0"


def test_new_prefix_wins_over_legacy():
    assert _setting({"WASL_REQUEST_TIMEOUT_S": "7", "KARESHAFAA_REQUEST_TIMEOUT_S": "9"}, "request_timeout_s") == "9.0"


def test_default_database_uses_new_name():
    assert _setting({}, "database_url").endswith("data/kareshafaa.db")


def test_pre_rename_model_artifacts_still_load(tmp_path):
    """Artifacts pickled as wasl.* (before the rename) must still unpickle; their SHA-256 is pinned in the registry."""
    import pickle

    import kareshafaa.ml.calibration as cal

    names = [n for n in dir(cal) if isinstance(getattr(cal, n), type) and getattr(cal, n).__module__ == cal.__name__]
    assert names, "expected at least one class in kareshafaa.ml.calibration"
    cls = getattr(cal, names[0])
    legacy = pickle.dumps(cls, protocol=0).replace(b"kareshafaa.ml.calibration", b"wasl.ml.calibration")
    assert b"wasl.ml.calibration" in legacy
    assert pickle.loads(legacy) is cls
