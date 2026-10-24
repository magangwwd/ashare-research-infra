"""Verify preserved Task 01 files without importing or starting the runtime."""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path, PurePosixPath


def verify(snapshot):
    if sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 12):
        raise RuntimeError('Snapshot requires CPython 3.12')
    root = Path(snapshot).resolve()
    manifest = json.loads((root / 'snapshot_manifest.json').read_text())
    if manifest['schema_version'] != '1.0' or not manifest['files']:
        raise ValueError('Unsupported or empty manifest')
    seen = set()
    for entry in manifest['files']:
        relative = PurePosixPath(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or str(relative) in seen:
            raise ValueError('Unsafe or duplicate manifest path')
        seen.add(str(relative))
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Unsafe snapshot member')
        payload = path.read_bytes()
        if len(payload) != entry['bytes'] or hashlib.sha256(payload).hexdigest() != entry['sha256']:
            raise ValueError('Snapshot checksum mismatch: ' + str(relative))
        if path.suffix == '.bin' and payload[:4] != importlib.util.MAGIC_NUMBER:
            raise ValueError('Incompatible recovery artifact: ' + str(relative))
    frozen = root / 'data_pipeline/recovered_oct08'
    for name, expected in json.loads((frozen / 'manifest.json').read_text()).items():
        if '/' in name or '\\' in name or name in ('.', '..'):
            raise ValueError('Unsafe recovery artifact name')
        path = frozen / (name + '.bin')
        if str(path.relative_to(root)) not in seen or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Recovery checksum mismatch: ' + name)
    return len(seen)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=Path(__file__).resolve().parents[1] / 'cloud_runtime/task01')
    args = parser.parse_args()
    print('VERIFIED_FILES', verify(args.snapshot))
