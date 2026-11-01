"""Guard the runtime dependency missing in Task01 live smoke run 41336548101."""

from pathlib import Path


def test_live_smoke_has_explicit_six_dependency():
    # Tushare imports six at runtime; a green synthetic unit suite alone did
    # not catch its absence in the production smoke step.
    import six

    assert six.PY3
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
    assert "six>=1.16,<2" in requirements.splitlines()
