"""Recovered original local engine; see recovered_oct08/README.md."""
from ._recovery_loader import load_original
load_original('runtime_guard', globals())
if __name__ == "__main__":
    raise SystemExit(main())
