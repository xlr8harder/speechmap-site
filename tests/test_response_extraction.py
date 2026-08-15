"""Response extraction shapes and the format-drift guard.

The site once shipped empty Model Response sections for six models because
their analysis rows used the Anthropic Messages response shape while the
parser only understood OpenAI choices. These tests pin both shapes and the
guard that fails the build if a new shape arrives.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from preprocess import iter_preprocessed_us_hard_data


def _row(response, qid="test_theme1", compliance="COMPLETE"):
    return {
        "model": "testlab/test-model",
        "question_id": qid,
        "compliance": compliance,
        "question": "test question",
        "domain": "Test Domain",
        "timestamp": "2026-01-01T00:00:00Z",
        "response": response,
    }


def _write_analysis(tmp_path, rows):
    path = tmp_path / "compliance_us_hard_testlab_test-model.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return tmp_path


CHOICES_SHAPE = {
    "choices": [{"message": {"role": "assistant", "content": "Visible answer text."}}]
}
MESSAGES_SHAPE = {
    "role": "assistant",
    "content": [
        {"type": "thinking", "thinking": "hidden deliberation"},
        {"type": "text", "text": "Visible answer text.", "citations": None},
        {"type": "redacted_thinking", "data": "…"},
    ],
    "stop_details": {"reason": "end_turn"},
}
STANDARDIZED_SHAPE = {
    "_llm_client": {
        "success": True,
        "standardized_response": {
            "content": [
                {"type": "thinking", "thinking": "hidden deliberation"},
                {"type": "text", "text": "Visible answer text."},
            ],
        },
    },
    "sequences": ["…"],
    "tinker": {},
}
UNKNOWN_SHAPE = {"output": [{"kind": "paragraph", "value": "some future format"}]}


def test_openai_choices_shape_extracts_text(tmp_path):
    records = list(iter_preprocessed_us_hard_data(
        str(_write_analysis(tmp_path, [_row(CHOICES_SHAPE, f"test_theme{i}") for i in range(12)]))))
    assert len(records) == 12
    assert all(r["response_text"] == "Visible answer text." for r in records)


def test_anthropic_messages_shape_extracts_text_blocks_only(tmp_path):
    records = list(iter_preprocessed_us_hard_data(
        str(_write_analysis(tmp_path, [_row(MESSAGES_SHAPE, f"test_theme{i}") for i in range(12)]))))
    assert len(records) == 12
    for r in records:
        assert r["response_text"] == "Visible answer text."
        assert "hidden deliberation" not in r["response_text"]


def test_llm_client_standardized_shape_extracts_text_blocks_only(tmp_path):
    records = list(iter_preprocessed_us_hard_data(
        str(_write_analysis(tmp_path, [_row(STANDARDIZED_SHAPE, f"test_theme{i}") for i in range(12)]))))
    assert len(records) == 12
    for r in records:
        assert r["response_text"] == "Visible answer text."
        assert "hidden deliberation" not in r["response_text"]


def test_unknown_shape_fails_the_build(tmp_path):
    with pytest.raises(RuntimeError, match="format drift"):
        list(iter_preprocessed_us_hard_data(
            str(_write_analysis(tmp_path, [_row(UNKNOWN_SHAPE, f"test_theme{i}") for i in range(12)]))))


def test_sparse_empty_responses_do_not_trip_guard(tmp_path):
    # fewer than 10 COMPLETE rows: not enough evidence to call drift
    records = list(iter_preprocessed_us_hard_data(
        str(_write_analysis(tmp_path, [_row(UNKNOWN_SHAPE, f"test_theme{i}") for i in range(5)]))))
    assert len(records) == 5
