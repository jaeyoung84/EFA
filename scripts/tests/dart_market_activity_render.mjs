// 자사주·내부자 공시 화면: 날짜 표기, 최신 날짜부터 날짜별 묶음, 시장 구분.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = (await readFile(new URL('../../app/frontend/js/views/dartMarketActivity.js', import.meta.url), 'utf8'))
  .replace(/^import \{ api \} from '..\/api.js';$/m, 'const api = {};');
const m = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

assert.equal(m.formatDisclosureDate('20261006'), '2026.10.06 (화)');
assert.equal(m.formatDisclosureDate('2026-10-03'), '2026.10.03 (토)');
assert.equal(m.formatDisclosureDate(''), '접수일 미확인');

const groups = m.groupByDate([
  { rcept_date: '20261002', rcept_no: '20261002000001' },
  { rcept_date: '20261006', rcept_no: '20261006000001' },
  { rcept_date: '20261006', rcept_no: '20261006000009' },
  { rcept_date: '20261005', rcept_no: '20261005000001' },
]);
assert.deepEqual(groups.map((g) => g.label), ['2026.10.06 (화)', '2026.10.05 (월)', '2026.10.02 (금)']);
assert.deepEqual(groups[0].items.map((i) => i.rcept_no), ['20261006000009', '20261006000001']);
console.log('dart_market_activity_render ok');
