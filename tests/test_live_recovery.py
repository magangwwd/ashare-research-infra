"""Recovery drill on real validated Parquet; opt-in after production acceptance."""
import hashlib
import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from data_pipeline.manifest import Store, atomic_json, read_json, read_session
from data_pipeline.validate import validate_task01


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
