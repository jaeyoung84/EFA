"""Fixed file layout inside the private KQS state directory (never inside Git)."""

from pathlib import Path


def state_paths(state_dir: Path) -> dict:
    state_dir = Path(state_dir)
    return {
        "quality_db": state_dir / "market-input-quality.sqlite3",
        "snapshot_db": state_dir / "recommendations.sqlite3",
        "attempts_db": state_dir / "publication-attempts.sqlite3",
        "pipeline_runs_db": state_dir / "pipeline-runs.sqlite3",
        "coverage_dir": state_dir / "coverage",
        "status_file": state_dir / "pipeline-status.json",
        "instrument_catalog": state_dir / "instrument-catalog.json",
        "recommendation_outcomes": state_dir / "recommendation-outcomes.json",
        "rebound_shadow": state_dir / "rebound-shadow.json",
        # Codex 수정: CODX-MA-002 (2026-10-16), RSI-OBV SMA60 상승 가설의 분리 저장소.
        "rsi_obv_sma60_slope_shadow": state_dir / "rsi-obv-sma60-slope-shadow.json",
        "signal_funnel": state_dir / "signal-funnel.json",
        "sector_rotation": state_dir / "sector-rotation.json",
    }
