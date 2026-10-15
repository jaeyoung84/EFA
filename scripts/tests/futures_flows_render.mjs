// 선물·수급 화면: 요약(최근일·5/20일 누적·미결제약정), 누적선, 표(억 원 환산·부호 색·escape).
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../app/frontend/js/views/futuresFlows.js', import.meta.url), 'utf8');
const m = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const day = (date, futQty, futMn, spotMn, extra = {}) => ({ date, futures_flows: { index: 6008.81, index_change_pct: -0.44,
  investors: { foreign: { qty: futQty, amount_mn: futMn }, institution: { qty: -futQty, amount_mn: -futMn } } },
  spot_flows: { index: 6008.81, investors: { foreign: { qty: 1, amount_mn: spotMn } } }, ...extra });
const rows = [day('2026-10-13', 1670, 389725, -100000),
  day('2026-10-14', 3557, 844524, -900000, { futures_bar: { close: 944.2 }, bar_contract: { name: 'F 202612' },
    close_snapshot: { open_interest: 154419, open_interest_change: 6654, basis: 0.6 } })];

const s = m.summarize(rows);
assert.equal(s.date, '2026-10-14');
assert.equal(s.futQty, 3557);
assert.equal(s.fut5Qty, 5227);
assert.equal(Math.round(s.futEok), 8445);                     // 844,524백만 원 = 8,445억
assert.equal(s.spot20Eok, -10000);
assert.equal(s.oi.open_interest, 154419);

assert.deepEqual(m.cumulative(rows).map((c) => [c.futures, c.spot]), [[3897, -1000], [12342, -10000]]);

const tiles = m.tilesHtml(s);
assert.ok(tiles.includes('+3,557계약') && tiles.includes('+8,445억') && tiles.includes('154,419계약') && tiles.includes('is-down'));
assert.equal(m.tilesHtml(m.summarize([])), '');

const table = m.tableHtml([{ ...rows[0], date: '<b>x</b>' }, rows[1]]);
assert.ok(table.indexOf('2026-10-14') < table.indexOf('&lt;b&gt;x'));      // 최신 날짜가 위
assert.ok(table.includes('944.20') && table.includes('-9,000억') && table.includes('154,419') && !table.includes('<b>x</b>'));
const crash = m.crashTableHtml([{ date: '2026-10-15', tier: 'A', kospi: 5554.22, r1: -7.57, r5: -18.37, foreign_futures_qty: 5544, foreign_spot_mn: -3758224 },
  { date: '2026-07-28', tier: 'B', kospi: 6023.66, r1: -4.2, r5: -9, foreign_futures_qty: -10, foreign_spot_mn: 1, bounce5_20: true, f20: 13.02, f60: -2 }]);
assert.ok(crash.includes('폭락(A)') && crash.includes('-7.57%') && crash.includes('+5,544') && crash.includes('-37,582억') && crash.includes('대기') && crash.includes('+13.02%') && crash.includes('예'));
assert.ok(m.crashTableHtml([]).includes('아직 급락 사건이 없습니다'));
console.log('futures_flows_render ok');
