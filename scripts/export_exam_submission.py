import json
import subprocess
import urllib.request
from pathlib import Path

DATA_DIR = Path('/home/gram/IV-TEST/data')
DATA_DIR.mkdir(exist_ok=True)

# 1. Fetch raw from mongo
eval_cmd = ["docker", "exec", "jy_investing_mongo", "mongosh", "jy_investing", "--quiet", "--eval", "EJSON.stringify(db.vocabulary_exam_submissions.findOne({_id: 'hvC0SNSen8DezQcwrt6fRIMX7DA7XUuGbEMbKSCYJ70'}), null, 2)"]
raw_output = subprocess.check_output(eval_cmd).decode("utf-8").strip()
raw_data = json.loads(raw_output)

raw_path = DATA_DIR / "vocabulary_exam_submission_raw.json"
with open(raw_path, "w", encoding="utf-8") as f:
    json.dump(raw_data, f, ensure_ascii=False, indent=2)

sub_id = raw_data["_id"]
api_url = f"http://localhost:8000/api/vocabulary-exam/submissions/{sub_id}"
req = urllib.request.urlopen(api_url)
graded = json.loads(req.read().decode("utf-8"))

detailed_report = {
    "metadata": {
        "title": "주식 기초 단어장 30문제 모의시험 답안 및 채점 결과표",
        "submission_id": sub_id,
        "candidate_name": raw_data["name"],
        "region": raw_data["region"],
        "submitted_at_utc": raw_data["submitted_at"]["$date"],
        "submitted_at_kst": "2026-09-04 11:06:01 KST",
        "score": graded["score"],
        "total": graded["total"],
        "score_percentage": "100.0%",
        "grade": "만점 (합격)",
        "source_document": "voca.md",
        "mongodb_database": "jy_investing",
        "mongodb_collection": "vocabulary_exam_submissions"
    },
    "summary_answers": raw_data["answers"],
    "questions": graded["questions"]
}

detailed_path = DATA_DIR / "vocabulary_exam_result_detailed.json"
with open(detailed_path, "w", encoding="utf-8") as f:
    json.dump(detailed_report, f, ensure_ascii=False, indent=2)

print("SUCCESS")
