"""Round 2: every patient-facing text table has the same keys in en, ar, hi and ur, no empty strings, and the
same {placeholders} as English, so no language ever falls back silently or loses a name/date/time."""
import importlib
import re
import string

import pytest

LANGS = ("en", "ar", "hi", "ur")
MODULES = ["kareshafaa.comms.content", "kareshafaa.comms.request_text", "kareshafaa.comms.self_service_text",
           "kareshafaa.comms.signin_text"]


def _lang_tables(obj, path=""):
    """Yield (path, {lang: subtree}) for every dict keyed by all four languages."""
    if isinstance(obj, dict):
        if set(LANGS) <= set(obj):
            yield path, obj
            return
        for k, v in obj.items():
            yield from _lang_tables(v, f"{path}.{k}")


def _leaves(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _leaves(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _leaves(v, f"{path}[{i}]")
    else:
        yield path, obj


def _fields(s: str) -> set[str]:
    try:
        return {f for _, f, _, _ in string.Formatter().parse(s) if f}
    except ValueError:
        return set(re.findall(r"\{(\w+)\}", s))


def _tables():
    for m in MODULES:
        mod = importlib.import_module(m)
        for name, val in vars(mod).items():
            if name.isupper() and isinstance(val, dict):
                for p, t in _lang_tables(val, name):
                    yield f"{m.rsplit('.', 1)[1]}:{p}", t


TABLES = list(_tables())


def test_tables_found():
    assert len(TABLES) >= 10


@pytest.mark.parametrize("name,table", TABLES, ids=[n for n, _ in TABLES])
def test_same_keys_no_empty_same_placeholders(name, table):
    en = dict(_leaves(table["en"]))
    for lang in LANGS[1:]:
        other = dict(_leaves(table[lang]))
        assert set(other) == set(en), (lang, sorted(set(en) ^ set(other))[:8])
        for k, v in en.items():
            ov = other[k]
            if isinstance(v, str):
                assert isinstance(ov, str) and (ov.strip() or not v.strip()), (lang, k, "empty")
                assert _fields(ov) == _fields(v), (lang, k, _fields(v), _fields(ov))
