import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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

    def test_evaluate_uses_three_state_logic_and_reason_codes(self):
        conn = _FakeConnection()

        process_addresses.evaluate(conn, uuid.uuid4(), "2025-12-31")

        query, _ = conn.cursor_instance.executed[0]
        self.assertIn("result_reason", query)
        self.assertIn("result_reason = EXCLUDED.result_reason", query)
        self.assertIn("WHEN evidence.has_qualifying_coverage THEN 'PASS'", query)
        self.assertIn("THEN 'FAIL'", query)
        self.assertIn("ELSE 'UNKNOWN'", query)
        self.assertIn("'qualifying_coverage'", query)
        self.assertIn("'below_download_threshold'", query)
        self.assertIn("'below_indoor_signal_threshold'", query)
        self.assertIn("'below_download_and_signal_threshold'", query)
        self.assertIn("'no_matching_polygon'", query)
        self.assertIn("'missing_signal_or_speed'", query)
        self.assertIn("FROM candidates", query)
        self.assertIn("WHERE is_qualifying", query)


class ExportResultsTests(unittest.TestCase):
    def test_export_results_defaults_to_unknown_for_missing_geocode_and_cache(self):
        captured: dict[str, object] = {}
        csv_written = {"value": False}

        class _FakeFrame:
            def to_csv(self, path, index=False):
                Path(path).write_text("address,city,state,zip\n")
                csv_written["value"] = True

        def _fake_read_sql_query(query, conn, params):
            captured["query"] = query
            captured["params"] = params
            return _FakeFrame()

        with TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "out.csv"
            with patch("process_addresses.pd.read_sql_query", side_effect=_fake_read_sql_query):
                process_addresses.export_results(
                    conn=object(),
                    batch_id=uuid.uuid4(),
                    release_id="2025-12-31",
                    path=output_path,
                )

            self.assertTrue(output_path.exists())
            self.assertTrue(csv_written["value"])

        query = str(captured["query"])
        self.assertIn("WHEN batch.geom IS NULL THEN 'UNKNOWN'", query)
        self.assertIn("coalesce(", query)
        self.assertIn("cache.carrier_code = 'att'),\n                    'UNKNOWN'", query)
        self.assertIn("cache.carrier_code = 'tmo'),\n                    'UNKNOWN'", query)
        self.assertIn("cache.carrier_code = 'vzw'),\n                    'UNKNOWN'", query)


if __name__ == "__main__":
    unittest.main()
