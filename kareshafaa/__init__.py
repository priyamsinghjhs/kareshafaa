"""KareShafaa - closing the screening-to-care loop (prototype software, not a certified medical device)."""

__version__ = "0.1.0"


def _accept_legacy_env() -> None:
    """Renamed from Wasl: settings use the KARESHAFAA_ prefix; legacy WASL_* variables (and WASL_* keys in .env) are still
    accepted as a fallback (KARESHAFAA_* wins if both are set)."""
    import os

    for key, value in list(os.environ.items()):
        if key.startswith("WASL_"):
            os.environ.setdefault("KARESHAFAA_" + key[len("WASL_"):], value)
    try:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and key.startswith("WASL_"):
                    os.environ.setdefault("KARESHAFAA_" + key[len("WASL_"):], value.strip().strip('"').strip("'"))
    except OSError:
        pass


_accept_legacy_env()


def _alias_legacy_package() -> None:
    """Renamed from Wasl: model artifacts pickled before the rename reference ``wasl.*`` modules (for example
    ``wasl.ml.calibration``). Their SHA-256 is pinned in the model registry, so they are not re-pickled; instead
    ``wasl`` / ``wasl.*`` imports resolve to the matching ``kareshafaa`` modules."""
    import importlib
    import importlib.abc
    import importlib.util
    import sys

    class _LegacyAlias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, fullname, path=None, target=None):  # noqa: D401
            if fullname == "wasl" or fullname.startswith("wasl."):
                return importlib.util.spec_from_loader(fullname, self)
            return None

        def create_module(self, spec):
            return importlib.import_module("kareshafaa" + spec.name[len("wasl"):])

        def exec_module(self, module):
            pass

    if not any(isinstance(f, _LegacyAlias) or type(f).__name__ == "_LegacyAlias" for f in sys.meta_path):
        sys.meta_path.insert(0, _LegacyAlias())


_alias_legacy_package()
