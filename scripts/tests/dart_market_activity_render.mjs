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

// 게시판: 시장 분리, 칸 안에서 날짜 구분, 지분 공시 숫자, escape.
assert.equal(m.marketOf({ market: 'KOSPI' }), 'KOSPI');
assert.equal(m.marketOf({ market: 'KONEX' }), '기타');
assert.equal(m.marketOf({}), '기타');
const column = m.columnHtml([
  { kind: 'insider_increase', corp_name: '<b>x</b>', stock_code: '111111', rcept_date: '20261005', rcept_no: '1', position: '-',
    reporter: '홍길동', relationship: '임원·주요주주', holding_count: 1000, change_count: 100, holding_rate: 1.2, change_rate: 0.1,
    dart_url: 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1' },
  { kind: 'treasury_decision', corp_name: '테스트', rcept_date: '20261006', rcept_no: '2', report_name: '주요사항보고서(자기주식 취득 결정)' },
]);
assert.ok(column.indexOf('2026.10.06 (화)') < column.indexOf('2026.10.05 (월)'));
assert.ok(column.includes('보유 <b>1,000주</b> (+100)') && column.includes('비율 <b>1.2%</b> (+0.1%p)'));
assert.ok(column.includes('홍길동 · 임원·주요주주</p>'));   // 직위 '-'는 숨김
assert.ok(column.includes('주요사항보고서(자기주식 취득 결정)') && !column.includes('<b>x</b>'));
assert.ok(m.columnHtml([]).includes('조건에 맞는 공시가 없습니다'));
console.log('dart_market_activity_board ok');
