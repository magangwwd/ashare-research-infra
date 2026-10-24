"""Invoke the idempotent Task 01 startup helper."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_pipeline.runtime_startup import main
if __name__=='__main__':
    try:main()
    except Exception as exc:print('ENSURE_RUNTIME_FAILED '+type(exc).__name__);raise SystemExit(1)
