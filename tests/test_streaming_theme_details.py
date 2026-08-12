import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from preprocess import StreamingThemeDetailWriter


def _record(theme, record_id):
    return {
        "id": record_id,
        "grouping_key": theme,
        "model": "example/model",
        "compliance": "COMPLETE",
    }


def test_streaming_theme_writer_publishes_complete_gzip_artifacts(tmp_path):
    output_dir = tmp_path / "theme_details"
    output_dir.mkdir()
    stale = output_dir / "stale.json.gz"
    stale.write_bytes(b"old")

    writer = StreamingThemeDetailWriter(str(output_dir))
    writer.add(_record("theme_one", "one"))
    writer.add(_record("theme_one", "two"))
    writer.add(_record("theme_two", "three"))

    # Existing artifacts remain untouched until the streamed corpus is valid.
    assert stale.read_bytes() == b"old"
    assert not (output_dir / "theme_one.json.gz").exists()

    assert writer.publish() == 2
    assert not stale.exists()
    with gzip.open(output_dir / "theme_one.json.gz", "rt", encoding="utf-8") as f:
        payload = json.load(f)
    assert [record["id"] for record in payload["records"]] == ["one", "two"]


def test_streaming_theme_writer_discard_preserves_existing_artifacts(tmp_path):
    output_dir = tmp_path / "theme_details"
    output_dir.mkdir()
    existing = output_dir / "existing.json.gz"
    existing.write_bytes(b"keep")

    writer = StreamingThemeDetailWriter(str(output_dir))
    writer.add(_record("theme_one", "one"))
    writer.discard()

    assert existing.read_bytes() == b"keep"
    assert not (output_dir / "theme_one.json.gz").exists()
