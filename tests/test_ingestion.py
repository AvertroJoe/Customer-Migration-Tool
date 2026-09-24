"""Unit tests for ingestion.py's header-row guessing and DataFrame
building - no network, no API key, no fixtures beyond plain Python data.
See README "Header row detection" for the design this protects."""

from __future__ import annotations

from app import ingestion


def _row(*cells: str) -> list[str]:
    return list(cells)


class TestGuessHeaderRow:
    def test_headers_first_sheet_guesses_row_zero(self):
        rows = [
            _row("Ref", "Title", "Description", "Status"),
            _row("R1", "First issue", "Some text", "Open"),
            _row("R2", "Second issue", "More text", "Acknowledged"),
        ]
        assert ingestion.guess_header_row(rows) == 0

    def test_title_row_then_blank_spacer_then_header(self):
        # The real shape hit in production (Qantas issue register): a
        # one-cell title, a fully blank row, then the real header.
        rows = [
            _row("Example issue register - illustrative sample data only", "", "", ""),
            _row("", "", "", ""),
            _row("Ref", "Title", "Description", "Status"),
            _row("R1", "First issue", "Some text", "Open"),
        ]
        assert ingestion.guess_header_row(rows) == 2

    def test_title_row_with_no_blank_spacer(self):
        rows = [
            _row("Some title text", "", "", ""),
            _row("Ref", "Title", "Description", "Status"),
            _row("R1", "First issue", "Some text", "Open"),
        ]
        assert ingestion.guess_header_row(rows) == 1

    def test_sheet_with_only_a_narrow_row_falls_back_to_zero(self):
        # Nothing ever looks "wide enough" to be a header - no confident
        # guess, so it falls back to row 0 rather than guessing wildly.
        rows = [_row("Just one column", "", "", "")]
        assert ingestion.guess_header_row(rows) == 0

    def test_empty_rows_list_falls_back_to_zero(self):
        assert ingestion.guess_header_row([]) == 0

    def test_wide_row_with_nothing_beneath_it_is_still_returned(self):
        # A wide row with no data after it (e.g. a header-only sheet) is
        # still a reasonable guess - better than always falling back to 0.
        rows = [_row("Ref", "Title", "Description", "Status")]
        assert ingestion.guess_header_row(rows) == 0


class TestBuildDataframe:
    def test_basic_shape(self):
        rows = [
            _row("Ref", "Title", "Status"),
            _row("R1", "First issue", "Open"),
            _row("R2", "Second issue", "Acknowledged"),
        ]
        df = ingestion.build_dataframe(rows, 0)
        assert list(df.columns) == ["Ref", "Title", "Status"]
        assert len(df) == 2
        assert df.iloc[0]["Title"] == "First issue"

    def test_blank_header_cells_get_fallback_names(self):
        rows = [_row("Ref", "", "Status"), _row("R1", "extra", "Open")]
        df = ingestion.build_dataframe(rows, 0)
        assert list(df.columns) == ["Ref", "Column 2", "Status"]

    def test_duplicate_header_cells_get_deduped_names(self):
        rows = [_row("Notes", "Notes", "Notes"), _row("a", "b", "c")]
        df = ingestion.build_dataframe(rows, 0)
        assert list(df.columns) == ["Notes", "Notes (2)", "Notes (3)"]

    def test_blank_data_rows_are_dropped(self):
        rows = [
            _row("Ref", "Title"),
            _row("R1", "First issue"),
            _row("", ""),
            _row("R2", "Second issue"),
        ]
        df = ingestion.build_dataframe(rows, 0)
        assert len(df) == 2
        assert list(df["Ref"]) == ["R1", "R2"]

    def test_ragged_rows_are_padded_and_truncated_to_header_width(self):
        rows = [
            _row("Ref", "Title", "Status"),
            _row("R1", "Short row"),  # too few cells
            _row("R2", "Long row", "Open", "extra"),  # too many cells
        ]
        df = ingestion.build_dataframe(rows, 0)
        assert df.iloc[0].tolist() == ["R1", "Short row", ""]
        assert df.iloc[1].tolist() == ["R2", "Long row", "Open"]

    def test_header_row_uses_title_row_not_header(self):
        # Confirms the header_row_index is actually respected, not just
        # always row 0 - build against the real title-then-header shape.
        rows = [
            _row("Some title", "", "", ""),
            _row("", "", "", ""),
            _row("Ref", "Title", "Description", "Status"),
            _row("R1", "First issue", "Some text", "Open"),
        ]
        df = ingestion.build_dataframe(rows, 2)
        assert list(df.columns) == ["Ref", "Title", "Description", "Status"]
        assert len(df) == 1

    def test_out_of_range_header_row_raises(self):
        rows = [_row("Ref", "Title")]
        try:
            ingestion.build_dataframe(rows, 5)
            assert False, "expected ValueError"
        except ValueError:
            pass
