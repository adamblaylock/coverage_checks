import unittest
import uuid

import process_addresses


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params):
        self.executed.append((query, params))


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()
        self.commit_called = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commit_called = True


class EvaluateCacheVersionTests(unittest.TestCase):
    def test_evaluate_filters_and_writes_current_cache_model_version(self):
        conn = _FakeConnection()
        batch_id = uuid.uuid4()
        release_id = "2025-12-31"

        process_addresses.evaluate(conn, batch_id, release_id)

        self.assertTrue(conn.commit_called)
        self.assertEqual(len(conn.cursor_instance.executed), 1)

        query, params = conn.cursor_instance.executed[0]
        self.assertIn("cache.cache_model_version = %s", query)
        self.assertIn("cache_model_version", query)
        self.assertIn("ON CONFLICT (address_hash, release_id, carrier_code) DO UPDATE", query)
        self.assertIn("cache_model_version = EXCLUDED.cache_model_version", query)

        self.assertEqual(
            params,
            (
                batch_id,
                release_id,
                process_addresses.COVERAGE_CACHE_MODEL_VERSION,
                release_id,
                release_id,
                process_addresses.COVERAGE_CACHE_MODEL_VERSION,
            ),
        )


if __name__ == "__main__":
    unittest.main()
