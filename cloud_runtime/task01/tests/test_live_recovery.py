"""Recovery drill on real validated Parquet; opt-in after production acceptance."""
import hashlib
import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from data_pipeline.manifest import Store, atomic_json, read_json, read_session
from data_pipeline.validate import validate_task01


def test_latest_real_trading_day_update_in_isolated_runner(tmp_path):
    import copy
    from data_pipeline.download import TushareClient
    from data_pipeline.run_task01 import Pipeline

    configured = os.environ.get('TASK01_RECOVERY_ROOT')
    if not configured or not os.environ.get('TUSHARE_TOKEN'):
        pytest.skip('requires validated production and a live Tushare environment')
    source_root = Path(configured).resolve()
    source = source_root / 'data/current'
    if not (source / 'clean').is_dir():
        source = source_root / 'data'
    config = copy.deepcopy(read_json(source_root / 'metadata/effective_config.json'))
    accepted = read_json(source_root / 'data/current/acceptance.json')
    day = date.fromisoformat(max(accepted['datasets']['daily']['sessions']))
    work = tmp_path / 'trading_day_replay'
    prior = work / 'data/.versions/previous'
    for layer in ('raw', 'clean'):
        shutil.copytree(source / layer, prior / layer, copy_function=os.link)
    (work / 'data/current').symlink_to('.versions/previous')
    # Replay only the real Critical update branch; do not re-download extensions/history.
    pipeline = Pipeline(work, config, TushareClient(config), day)
    pipeline.bootstrap()
    assert pipeline.mode == 'DAILY_UPDATE'
    pipeline.critical_recent()
    report = validate_task01(pipeline.stage, config, day,
                             dates=pipeline.sessions[-config['download']['lookback_trading_days']:])
    assert report['passed'], report['errors']
    pipeline.store.publish(report)
    assert (work / 'data/current').resolve() == pipeline.stage.resolve()
    atomic_json(source_root / 'metadata/trading_day_replay.json', {
        'passed': True, 'mode': pipeline.mode, 'replayed_trade_date': str(day),
        'live_tushare': True, 'report': report,
        'source': 'latest closed real trading day; isolated runner; not a scheduled same-day run',
    })


def test_real_production_rejects_bad_stage_and_survives_pointer_failure(tmp_path, monkeypatch):
    configured = os.environ.get('TASK01_RECOVERY_ROOT')
    if not configured:
        pytest.skip('requires an explicitly supplied validated production directory')
    source_root = Path(configured).resolve()
    source = source_root / 'data/current'
    # Exported artifacts flatten raw/clean, while runner checkpoints retain current.
    if not (source / 'clean').is_dir():
        source = source_root / 'data'
    config = read_json(source_root / 'metadata/effective_config.json')
    accepted = read_json(source_root / 'data/current/acceptance.json')
    as_of = date.fromisoformat(accepted['as_of'])
    assert accepted['passed'] and validate_task01(source, config, as_of)['passed']
    original = {p.relative_to(source): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.rglob('*.parquet')}
    assert original
    work = tmp_path / 'drill'
    versions = work / 'data/.versions'
    production = versions / 'previous'
    versions.mkdir(parents=True)
    for layer in ('raw', 'clean'):
        shutil.copytree(source / layer, production / layer, copy_function=os.link)
    shutil.copyfile(source_root / 'data/current/acceptance.json', production / 'acceptance.json')
    (work / 'data/current').symlink_to('.versions/previous')
    store = Store(work, config)
    stage = store.begin()
    day = date.fromisoformat(max(accepted['datasets']['daily']['sessions']))
    good_session = read_session(stage, 'daily', day)
    store.save_clean('daily', good_session.iloc[:1], replace_dates={day})
    failed = validate_task01(stage, config, as_of)
    assert not failed['passed']
    assert any('coverage=' in reason for reason in failed['errors'])
    with pytest.raises(ValueError, match='passed Task 01 acceptance'):
        store.publish(failed)
    assert (work / 'data/current').resolve() == production.resolve()
    assert all(hashlib.sha256((source / p).read_bytes()).hexdigest() == checksum
               for p, checksum in original.items())
    resumed = Store(work, config)
    assert resumed.begin() == stage
    resumed.save_clean('daily', good_session, replace_dates={day})
    repaired = validate_task01(stage, config, as_of)
    assert repaired['passed']
    replace = os.replace

    def fail_pointer_switch(old, new):
        if Path(new) == work / 'data/current':
            raise OSError('simulated production pointer interruption')
        return replace(old, new)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', fail_pointer_switch)
        with pytest.raises(OSError, match='pointer interruption'):
            resumed.publish(repaired)
    assert (work / 'data/current').resolve() == production.resolve()
    assert all(hashlib.sha256((production / p).read_bytes()).hexdigest() == checksum
               for p, checksum in original.items())
    resumed.publish(repaired)
    assert (work / 'data/current').resolve() == stage.resolve()
    assert validate_task01(stage, config, as_of)['passed']
    atomic_json(source_root / 'metadata/recovery_drill.json', {
        'passed': True, 'as_of': str(as_of), 'parquet_files_checked': len(original),
        'bad_coverage_publication_blocked': True, 'previous_files_unchanged': True,
        'pointer_failure_preserved_previous': True, 'resume_and_repair_passed': True,
        'source': 'real validated Parquet; isolated runner staging; no API fault injection',
    })
