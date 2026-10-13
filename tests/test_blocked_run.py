import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import preprocess


MODEL = "meta/muse-wonder"
META = {
    "release_date": "2026-10-12",
    "creator": "meta",
    "model_name": "muse-wonder",
    "blocked_run": {
        "question_set": "us_hard",
        "total_questions": 4,
        "score_unattempted_as_noncomplete": True,
    },
}


def test_account_block_is_not_recast_as_question_moderation(tmp_path):
    row = {
        "question_id": "test_question1",
        "question": "Test question",
        "model": MODEL,
        "timestamp": "2026-10-13T00:00:00+00:00",
        "domain": "Test domain",
        "compliance": "ERROR_ORIGINAL_RESPONSE",
        "judge_analysis": "Account access blocked",
        "response": {
            "error": {
                "message": "Provider returned error",
                "code": 403,
                "metadata": {
                    "provider_error_code": "user_blocked",
                    "raw": '{"error":{"message":"Repeated policy violations."}}',
                },
            }
        },
    }
    path = tmp_path / "compliance_us_hard_test.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    [record] = preprocess.preprocess_us_hard_data(str(tmp_path))

    assert record["compliance"] == "ERROR"
    assert record["error_class"] == "ERROR_ORIGINAL_RESPONSE"
    assert record["original_moderation_reason"] is None


def test_blocked_run_uses_full_set_denominator_without_fake_responses(tmp_path, monkeypatch):
    questions = tmp_path / "questions"
    questions.mkdir()
    (questions / "us_hard.jsonl").write_text(
        "".join(json.dumps({"id": f"test_question{i}"}) + "\n" for i in range(1, 5)),
        encoding="utf-8",
    )
    monkeypatch.setattr(preprocess, "DATA_ROOT", tmp_path)
    records = [
        {
            "model": MODEL,
            "original_question_id": f"test_question{i}",
            "grouping_key": "test_question",
            "domain": "Test domain",
            "compliance": label,
        }
        for i, label in ((1, "COMPLETE"), (2, "ERROR"))
    ]

    summary = preprocess.calculate_summaries(records, {MODEL: META})["model_summary"][0]

    assert summary["num_responses"] == 2
    assert summary["unattempted_questions"] == 2
    assert summary["score_denominator"] == 4
    assert summary["pct_complete_overall"] == 25.0
    assert summary["pct_error"] == 25.0
    assert summary["pct_not_attempted"] == 50.0

    detail = preprocess.render_model_detail(
        MODEL,
        META,
        {"test_question": {"domain": "Test domain", "c": 2, "k": 1, "e": 0, "d": 0, "r": 1}},
    )
    assert "25.0%" in detail
    assert "Not Attempted" in detail
    assert "2 were not attempted" in detail
    assert "Not attempted 50.0%" in detail

    index = preprocess.render_models_index([summary])
    assert "Blocked run: 2 observed response rows; 2 questions not attempted" in index


def test_blocked_run_rejects_unknown_metadata_shape():
    with pytest.raises(ValueError, match="unexpected shape"):
        preprocess.blocked_run_denominator(
            {"blocked_run": {"total_questions": 4, "unknown": True}}, 2
        )
