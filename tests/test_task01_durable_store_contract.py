"""Offline contract tests for durable ledger compare-and-swap.

No Drive writes or production authorization. A real backend must pass
the same race contract before being connected to a publisher.
"""
import unittest
from threading import Lock, Barrier, Thread
from copy import deepcopy


class FakeConditionalStore:
    """Atomic in-memory reference model, NOT a production backend."""
    def __init__(self):
        self.lock = Lock()
        self.generation = 0
        self.value = None

    def read(self):
        with self.lock:
            return self.generation, deepcopy(self.value)

    def compare_and_swap(self, expected, value):
        with self.lock:
            if expected != self.generation:
                return False
            self.value = deepcopy(value)
            self.generation += 1
            return True


class DurableStoreContractTests(unittest.TestCase):
    def test_two_writers_only_one_wins(self):
        store = FakeConditionalStore()
        barrier = Barrier(2)
        results = []
        def writer(owner):
            generation, _ = store.read()
            barrier.wait(timeout=5)
            results.append(store.compare_and_swap(generation, {"owner": owner}))
        threads = [Thread(target=writer, args=(owner,)) for owner in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(sorted(results), [False, True])

    def test_stale_writer_cannot_replace_new_generation(self):
        store = FakeConditionalStore()
        old, _ = store.read()
        self.assertTrue(store.compare_and_swap(old, {"owner": "a"}))
        current, _ = store.read()
        self.assertTrue(store.compare_and_swap(current, {"owner": "b"}))
        self.assertFalse(store.compare_and_swap(current, {"owner": "a"}))
        self.assertEqual(store.read()[1], {"owner": "b"})

    def test_read_snapshot_cannot_mutate_store(self):
        store = FakeConditionalStore()
        self.assertTrue(store.compare_and_swap(0, {"receipts": []}))
        _, snapshot = store.read()
        snapshot["receipts"].append("unverified")
        self.assertEqual(store.read()[1], {"receipts": []})


if __name__ == "__main__":
    unittest.main()
