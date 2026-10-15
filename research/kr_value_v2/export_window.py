"""서버 호스트에서 실행: KRX 일봉 DB에서 시작일 이후 구간만 같은 스키마의 작은 sqlite로 복사(읽기 전용, 날짜 인덱스 사용).

usage: python3 export_window.py <krx_daily.sqlite3> <out.sqlite3> <YYYY-MM-DD>
"""
import sqlite3
import sys

src, out, start = sys.argv[1:4]
con = sqlite3.connect(out)
con.execute(f"ATTACH DATABASE 'file:{src}?mode=ro' AS src")
schema = con.execute("SELECT sql FROM src.sqlite_master WHERE type='table' AND name='daily_prices'").fetchone()[0]
con.execute(schema)
con.execute("INSERT INTO daily_prices SELECT * FROM src.daily_prices WHERE trade_date >= ?", (start,))
con.commit()
n, lo, hi = con.execute("SELECT count(*), min(trade_date), max(trade_date) FROM daily_prices").fetchone()
print(f"rows {n} {lo}~{hi}")
