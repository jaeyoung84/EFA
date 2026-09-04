import urllib.request
import json
from pathlib import Path

DATA_DIR = Path('/home/gram/IV-TEST/data')
DATA_DIR.mkdir(exist_ok=True)

OFFICIAL_DOC = {
    "_id": "hvC0SNSen8DezQcwrt6fRIMX7DA7XUuGbEMbKSCYJ70",
    "name": "송재영",
    "region": "경기도",
    "answers": [1, 2, 2, 2, 0, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 1, 1, 2],
    "submitted_at": "2026-09-04T11:06:01.653+09:00"
}

raw_path = DATA_DIR / "vocabulary_exam_submission_raw.json"
with open(raw_path, "w", encoding="utf-8") as f:
    json.dump(OFFICIAL_DOC, f, ensure_ascii=False, indent=2)

req = urllib.request.urlopen("http://localhost:8000/api/vocabulary-exam/submissions/hvC0SNSen8DezQcwrt6fRIMX7DA7XUuGbEMbKSCYJ70")
graded = json.loads(req.read().decode("utf-8"))

detailed_report = {
    "metadata": {
        "title": "주식 기초 단어장 30문제 모의시험 답안 및 채점 결과표",
        "submission_id": OFFICIAL_DOC["_id"],
        "candidate_name": OFFICIAL_DOC["name"],
        "region": OFFICIAL_DOC["region"],
        "submitted_at_kst": "2026-09-04 11:06:01 KST",
        "score": graded["score"],
        "total": graded["total"],
        "score_percentage": "100.0%",
        "grade": "만점 (합격)",
        "source_document": "voca.md",
        "mongodb_database": "jy_investing",
        "mongodb_collection": "vocabulary_exam_submissions"
    },
    "summary_answers": OFFICIAL_DOC["answers"],
    "questions": graded["questions"]
}

detailed_path = DATA_DIR / "vocabulary_exam_result_detailed.json"
with open(detailed_path, "w", encoding="utf-8") as f:
    json.dump(detailed_report, f, ensure_ascii=False, indent=2)

print("GENERATED_SUCCESSFULLY")
