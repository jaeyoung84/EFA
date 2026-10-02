"""backend 이미지 패키지 버전 고정(2026-10-02): requirements.txt의 모든 패키지가 lock에 정확한 버전으로 있어야 한다."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _name(line: str) -> str:
    return re.split(r"[\[<>=!~ ;@]", line.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]


def test_every_requirement_is_pinned_in_the_lock():
    lock = _lines(ROOT / "requirements.lock.txt")
    assert lock and all(re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+", line) for line in lock), "lock은 이름==버전만"
    pinned = {_name(line) for line in lock}
    missing = sorted({_name(line) for line in _lines(ROOT / "requirements.txt")} - pinned)
    assert not missing, f"lock에 없는 패키지: {missing}"


def test_dockerfile_installs_with_the_lock():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "FROM python:3.12-slim-trixie" in dockerfile
    assert "pip install -r requirements.txt -c requirements.lock.txt" in dockerfile and "pip check" in dockerfile
