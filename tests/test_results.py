import csv

import openpyxl
import pytest

from batchmortal.results import (
    ResultWriter,
    backfill_result_metadata,
    get_processed_uuids,
    read_result_rows,
)


@pytest.mark.parametrize("output_format", ["csv", "xlsx"])
def test_backfill_adds_pt_without_overwriting_analysis(tmp_path, output_format):
    path = tmp_path / f"results.{output_format}"
    writer = ResultWriter(str(path), output_format)
    writer.write_row(
        {
            "uuid": "game-uuid_a123",
            "mode": "direct",
            "recordType": "unknown",
            "rating": 88.5,
            "ptDelta": "",
            "startTime": "",
        }
    )
    writer.close()

    count = backfill_result_metadata(
        str(path),
        output_format,
        {
            "game-uuid_a123": {
                "startTime": "2026-09-01 12:00:00",
                "mode": 2,
                "recordType": "friend",
                "ptDelta": -16,
                "placement": 3,
                "playerLevelScore": 1296,
                "rating": 1,
            }
        },
    )

    assert count == 1
    if output_format == "csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
    else:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            values = list(workbook.active.iter_rows(values_only=True))
            row = dict(zip(values[0], values[1]))
        finally:
            workbook.close()
    assert str(row["rating"]) == "88.5"
    assert str(row["mode"]) == "2"
    assert str(row["ptDelta"]) == "-16"
    assert str(row["placement"]) == "3"
    assert str(row["playerLevelScore"]) == "1296"
    assert row["recordType"] == "friend"
    assert (tmp_path / f"results.backup.{output_format}").exists()


def test_xlsx_writer_saves_each_result_and_keeps_rolling_backup(tmp_path):
    path = tmp_path / "results.xlsx"
    with ResultWriter(str(path), "xlsx") as writer:
        writer.write_row({"uuid": "first", "rating": 88.0})

    writer = ResultWriter(str(path), "xlsx")
    try:
        writer.write_row({"uuid": "second", "rating": 90.0})
        assert {row["uuid"] for row in read_result_rows(str(path), "xlsx")} == {
            "first",
            "second",
        }
        backup = tmp_path / "results.backup.xlsx"
        assert backup.exists()
        assert [row["uuid"] for row in read_result_rows(str(backup), "xlsx")] == [
            "first"
        ]
    finally:
        writer.close()


@pytest.mark.parametrize("output_format", ["csv", "xlsx"])
def test_processed_uuid_requires_successful_rating(tmp_path, output_format):
    path = tmp_path / f"results.{output_format}"
    with ResultWriter(str(path), output_format) as writer:
        writer.write_row({"uuid": "success", "rating": 88.0})
        writer.write_row({"uuid": "invalid", "rating": "INVALID"})
        writer.write_row({"uuid": "failed", "rating": "ERROR"})
        writer.write_row({"uuid": "blank", "rating": ""})

    assert get_processed_uuids(str(path), output_format) == {"success", "invalid"}
