"""Original cached full-backfill integration tests, pinned during recovery."""
import marshal,hashlib
from pathlib import Path
payload=(Path(__file__).parents[1]/'data_pipeline/recovered_oct08/test_full_backfill.bin').read_bytes()
assert hashlib.sha256(payload).hexdigest()=='71516edd5166b0dcaadad9b9dc4fce8b7245abd7b603e22da6466f40b6f9a31f'
exec(marshal.loads(payload[16:]),globals())
