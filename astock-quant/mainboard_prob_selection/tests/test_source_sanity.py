import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sources_compile():
    for p in sorted(ROOT.glob('*.py')):
        ast.parse(p.read_text(encoding='utf-8-sig'), filename=str(p))


def test_paths_are_configurable():
    core = (ROOT / 'core.py').read_text(encoding='utf-8')
    build = (ROOT / '01_build_panel.py').read_text(encoding='utf-8')
    weekly = (ROOT / '09_weekly_signal.py').read_text(encoding='utf-8')
    assert 'PROB_GOLD_ROOT' in core and 'PROB_GOLD_ROOT' in build
    assert 'PROB_AQ_ROOT' in core
    assert 'PROB_REQUIRE_OFFICIAL_CALENDAR' in core
    assert 'V7_LIVE_DIR' in weekly


def test_no_redistributable_data():
    assert not list(ROOT.rglob('*.parquet'))
