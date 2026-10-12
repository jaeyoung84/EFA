"""EINT-036 S&P 500 구성 이력: 표 파싱(rowspan), 역순 복원, 티커 변경 별칭, 월말 검토 표시."""
import pytest

from app.backend import us_universe as u

CONSTITUENTS = """<table id="constituents"><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th>
<th>Headquarters Location</th><th>Date added</th><th>CIK</th><th>Founded</th></tr>
""" + "".join(f"<tr><td>{s}</td><td>{s} Inc.</td><td>IT</td><td>Software</td><td>NY</td><td>2010-01-01</td><td>1</td><td>1900</td></tr>"
              for s in ("AAA", "BBB", "CCC", "NEWCO", "BRK.B")) + "</table>"

CHANGES = """<table id="changes"><tr><th rowspan="2">Effective Date</th><th colspan="2">Added</th><th colspan="2">Removed</th>
<th rowspan="2">Reason</th><th rowspan="2">Refs</th></tr><tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td>March 1, 2024</td><td>CCC</td><td>Ccc</td><td>DDD</td><td>Ddd</td><td>cap</td><td><sup>[1]</sup></td></tr>
<tr><td>June 1, 2020</td><td>OLDCO</td><td>Old Co</td><td>EEE</td><td>Eee</td><td>acq</td><td>[2]</td></tr>
<tr><td rowspan="2">February 1, 2018</td><td>BBB</td><td>Bbb</td><td>FFF</td><td>Fff</td><td>cap</td><td>[3]</td></tr>
<tr><td></td><td></td><td>GGG</td><td>Ggg</td><td>cap</td><td>[3]</td></tr>
<tr><td>May 5, 2010</td><td>AAA</td><td>Aaa</td><td>ZZZ</td><td>Zzz</td><td>old</td><td>[4]</td></tr>
""" + "".join(f"<tr><td>January {d}, 2009</td><td>X{d}</td><td>x</td><td>Y{d}</td><td>y</td><td>pad</td><td>[9]</td></tr>" for d in range(1, 29)) * 4 + "</table>"

ALIASES = {"OLDCO": [("2023-05-01", "NEWCO")]}


def test_parsers_expand_rowspan_and_keep_class_tickers():
    current = u.parse_constituents(CONSTITUENTS.replace("</table>", "") + "".join(
        f"<tr><td>P{i:03d}</td><td>p</td><td>x</td><td>x</td><td>x</td><td>2010-01-01</td><td>1</td><td>1</td></tr>" for i in range(450)) + "</table>")
    assert {"AAA", "BRK.B"} <= {row["symbol"] for row in current}
    changes = u.parse_changes(CHANGES)
    feb = [c for c in changes if c["date"] == "2018-02-01"]
    assert [(c["added"], c["removed"]) for c in feb] == [("BBB", "FFF"), (None, "GGG")]   # 날짜 rowspan이 다음 행에 채워진다
    assert u.resolve("OLDCO", "2020-06-01", ALIASES) == "NEWCO" and u.resolve("OLDCO", "2024-01-01", ALIASES) == "OLDCO"


def test_reconstruction_runs_changes_backwards_with_aliases():
    current = [{"symbol": s, "name": s} for s in ("AAA", "BBB", "CCC", "NEWCO")]
    rebuilt = u.reconstruct(current, u.parse_changes(CHANGES), ALIASES)
    by = {(row["symbol"], row["start"]): row for row in rebuilt["intervals"]}
    assert by[("CCC", "2024-03-01")]["end"] is None
    assert by[("DDD", u.START)]["end"] == "2024-03-01"
    assert by[("NEWCO", "2020-06-01")]["wiki_symbols"] == ["NEWCO", "OLDCO"]     # 옛 티커로 편입, 같은 회사 한 번만
    assert by[("EEE", u.START)]["end"] == "2020-06-01"
    assert by[("GGG", u.START)]["end"] == "2018-02-01" and by[("BBB", "2018-02-01")]["end"] is None
    assert ("AAA", u.START) in by and rebuilt["anomalies"] == []                 # 2016 이전 변경은 되돌리지 않는다
    assert u.members_on(rebuilt["intervals"], "2019-01-02") == {"AAA", "BBB", "DDD", "EEE"}
    without_alias = u.reconstruct(current, u.parse_changes(CHANGES), {})
    assert without_alias["anomalies"][0]["kind"] == "added_not_member"


def test_month_end_counts_outside_range_are_flagged():
    report = u.month_end_report([{"symbol": f"S{i}", "start": u.START, "end": None} for i in range(500)]
                                + [{"symbol": "X", "start": "2020-01-01", "end": "2020-02-01"}], ["2019-12-31", "2020-01-31"])
    assert [row["review"] for row in report] == [False, False]
    assert u.month_end_report([{"symbol": "A", "start": u.START, "end": None}], ["2020-01-31"])[0]["review"] is True


def test_missing_tables_raise():
    with pytest.raises(u.UniverseError):
        u.parse_constituents("<table></table>")
    with pytest.raises(u.UniverseError):
        u.parse_changes("<p>none</p>")
