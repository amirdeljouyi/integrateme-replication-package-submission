"""Fixtures shared across the pipeline test modules."""

from __future__ import annotations

import csv
from pathlib import Path


SOURCE = """\
package sample;

class SampleTest {
    @ParameterizedTest
    @CsvSource(value = {
        "left(",
        "right)"
    })
    void manualParameterized(String value) {
    }

    @Test
    void manualZeroArg() {
    }

    // added test
    @org.junit.jupiter.api.Test
    void adoptedZeroArg() {
        SampleTest.helper();
    }

    @ParameterizedTest
    @ValueSource(strings = {
        "("
    })
    void adoptedParameterized(String value) {
    }

    @ParameterizedTest
    void adoptedZeroArgParameterized() {
    }

    @Nested
    class NestedTests {
        @Test
        void adoptedNested() {
        }
    }

    private static void helper() {
    }
}
"""


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
