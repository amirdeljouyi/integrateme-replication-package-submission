import unittest

from pathlib import Path
import csv
import tempfile

from support import write_csv

from src.steps.coverage_compare import _append_existing_coverage_rows, _matching_coverage_rows


class CoverageCompareTest(unittest.TestCase):
    def test_existing_coverage_rows_can_be_reused_with_variant_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fields = [
                "repo",
                "fqcn",
                "variant",
                "line_percentage_coverage",
                "line_covered",
                "line_total",
                "branch_percentage_coverage",
                "branch_covered",
                "branch_total",
                "failed",
                "timeout",
                "skipped",
            ]
            source = root / "source.csv"
            write_csv(
                source,
                fields,
                [
                    {
                        "repo": "sample/repo",
                        "fqcn": "sample.Target",
                        "variant": "pr-tests",
                        "line_covered": "3",
                        "line_total": "10",
                        "branch_covered": "1",
                        "branch_total": "4",
                        "failed": "0",
                        "timeout": "0",
                        "skipped": "0",
                    }
                ],
            )
            destination = root / "destination.csv"
            write_csv(destination, fields, [])

            rows = _matching_coverage_rows(
                source,
                repo="sample/repo",
                fqcn="sample.Target",
                variants={"pr-tests"},
            )
            _append_existing_coverage_rows(destination, rows, variant_override="pr-tests-reduced")

            with destination.open(encoding="utf-8", newline="") as handle:
                written = list(csv.DictReader(handle))
            self.assertEqual(written[0]["variant"], "pr-tests-reduced")
            self.assertEqual(written[0]["line_percentage_coverage"], "30.000000")


if __name__ == "__main__":
    unittest.main()
