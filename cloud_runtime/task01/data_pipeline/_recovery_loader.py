"""Load checksum-pinned local CPython recovery artifacts."""
import hashlib, importlib.util, json, marshal
from pathlib import Path

def load_original(name, namespace):
    base=Path(__file__).parent/'recovered_oct08'
    payload=(base/(name+'.bin')).read_bytes()
    hashes=json.loads((base/'manifest.json').read_text())
    if payload[:4]!=importlib.util.MAGIC_NUMBER or hashlib.sha256(payload).hexdigest()!=hashes[name]:
        raise RuntimeError('Recovered engine artifact is incompatible or corrupt')
    identity=namespace['__name__']
    namespace['__name__']='data_pipeline.'+name
    try:exec(marshal.loads(payload[16:]),namespace)
    finally:namespace['__name__']=identity
    import sys
    module=sys.modules.get('data_pipeline.'+name)
    if name=='drive_publish':
        from .recovery_transport import install
        install(module)
    elif name=='drive_cache':
        from .recovery_cache import install
        install(module)
    elif name=='checkpoint':
        from .recovery_runner import patch_checkpoint
        patch_checkpoint(module)
    elif name=='full_backfill':
        from .recovery_runner import main
        namespace['main']=main
    elif name=='manifest':
        from .recovery_scope import install
        install(module)
    elif name=='runtime_guard':
        original_policy=namespace['retry_policy']
        def recovery_policy(code, reason, crashes=0, upload_failures=0):
            if 'Drive request failed (403)' in reason and any(x in reason for x in ['userRateLimitExceeded','rateLimitExceeded']):
                return original_policy(code,'Drive request failed (403); operation=PUT upload',crashes,upload_failures)
            return original_policy(code,reason,crashes,upload_failures)
        namespace['retry_policy']=recovery_policy
