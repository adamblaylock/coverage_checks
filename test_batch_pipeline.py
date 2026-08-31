import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import call, patch

import batch_pipeline


class DiscoverInputFilesTests(unittest.TestCase):
    def test_discovers_non_recursive_csvs_sorted_largest_first(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "small.csv").write_text("a\n")
            (root / "large.csv").write_text("a\nb\nc\n")
            nested = root / "nested"
            nested.mkdir()
            (nested / "ignored.csv").write_text("a\nb\nc\nd\n")
            (root / "ignored.txt").write_text("x")

            files = batch_pipeline.discover_input_files(root)

        self.assertEqual([path.name for path in files], ["large.csv", "small.csv"])


class RunPipelineForFileTests(unittest.TestCase):
    def test_runs_expected_pipeline_commands(self):
        py = Path("/usr/bin/python3")
        input_path = Path("/tmp/inputs/addresses.csv")
        output_path = Path("/tmp/output/addresses_coverage_results.csv")
        coverage_root = Path("/tmp/coverage")
        download_dir = Path("/tmp/downloads")

        with patch("batch_pipeline.run") as run_mock, patch(
            "batch_pipeline.read_selected_release", return_value="2025/12/31"
        ):
            release = batch_pipeline.run_pipeline_for_file(
                py,
                input_path,
                output_path,
                providers="att,tmo",
                as_of="2025-12-31",
                force_download=True,
                coverage_root=coverage_root,
                download_dir=download_dir,
            )

        self.assertEqual(release, "2025/12/31")
        run_mock.assert_has_calls(
            [
                call(py, "init_database.py"),
                call(
                    py,
                    "sync_fcc_data.py",
                    "--input",
                    input_path,
                    "--providers",
                    "att,tmo",
                    "--download-dir",
                    download_dir,
                    "--coverage-root",
                    coverage_root,
                    "--as-of",
                    "2025-12-31",
                    "--force",
                ),
                call(
                    py,
                    "load_postgis.py",
                    "--coverage-dir",
                    coverage_root / "2025_12_31",
                    "--input",
                    input_path,
                    "--release-id",
                    "2025/12/31",
                    "--replace-states",
                    "--subdivide",
                ),
                call(
                    py,
                    "process_addresses.py",
                    "--input",
                    input_path,
                    "--output",
                    output_path,
                    "--release-id",
                    "2025/12/31",
                ),
            ]
        )


class RunBatchTests(unittest.TestCase):
    def test_starts_docker_once_and_cleans_up_after_each_file(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_dir = root / "inputs"
            output_dir = root / "outputs"
            input_dir.mkdir()
            (input_dir / "large.csv").write_text("a\nb\nc\n")
            (input_dir / "small.csv").write_text("a\n")

            with patch("batch_pipeline.run") as run_mock, patch(
                "batch_pipeline.run_pipeline_for_file",
                side_effect=["2025/12/31", subprocess.CalledProcessError(1, ["boom"])],
            ) as pipeline_mock, patch("batch_pipeline.cleanup_artifacts") as cleanup_mock:
                exit_code = batch_pipeline.main(
                    ["--input-dir", str(input_dir), "--output-dir", str(output_dir)]
                )

        self.assertEqual(exit_code, 1)
        run_mock.assert_called_once_with("docker", "compose", "up", "-d")
        self.assertEqual(
            [args.args[1].name for args in pipeline_mock.call_args_list],
            ["large.csv", "small.csv"],
        )
        self.assertEqual(
            [args.args[2].name for args in pipeline_mock.call_args_list],
            ["large_coverage_results.csv", "small_coverage_results.csv"],
        )
        self.assertEqual(cleanup_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
