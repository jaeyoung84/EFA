// 외부 추천 기록 화면: 미리보기 칩(체크), 명단 카드 요약·종목표, 연결 성과 카드, escape.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = (await readFile(new URL('../../app/frontend/js/views/externalPicks.js', import.meta.url), 'utf8'))
  .replace(/^import .*$/m, 'const authRequest = async () => ({}); const isAuthenticated = () => true; const openAuthModal = () => {};');
const m = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const preview = m.previewHtml({ mode: 'tokens', recognized: [{ ticker: 'AAPL', name: '<b>Apple</b>', market: 'US' }, { ticker: '005930.KS', name: '삼성전자', market: 'KR' }], unrecognized: ['ZZZ'] });
assert.ok(preview.includes('data-xp-pick="1" checked') && preview.includes('· 국내') && preview.includes('ZZZ') && !preview.includes('<b>Apple</b>'));

const list = { id: 'x1', list_name: '10월 <i>', source: 'InvestingPro ProPicks', as_of: '2026-10-14', count: 2, pending: ['NVDA'],
  summary: { return_pct: 3.2, benchmark_return_pct: 1.1, excess_pct: 2.1, beat_rate_pct: 50, priced: 1, sessions: 12 },
  positions: [{ ticker: 'AAPL', name: 'Apple', entry_date: '2026-10-15', return_pct: 3.2, benchmark_return_pct: 1.1, excess_pct: 2.1, horizons: {} }] };
const card = m.listCardHtml(list);
assert.ok(card.includes('+3.20%') && card.includes('is-up') && card.includes('시세 대기: NVDA') && card.includes('data-xp-delete="x1"') && !card.includes('<i>'));

const track = m.trackHtml('ProPicks', { months: 2, return_pct: -1.5, benchmark_return_pct: 2, excess_pct: -3.5 });
assert.ok(track.includes('-1.50%') && track.includes('is-down') && track.includes('12개월 이상'));
assert.equal(m.trackHtml('x', { months: 0 }), '');

// PDF: 파일 이름 → 출처, 달 목록(escape), 기록 날짜는 그달 1일(미래면 오늘)
assert.equal(m.sourceFromFilename('한국 대장주 가치 투자주.pdf'), 'InvestingPro 한국 대장주 가치');
assert.equal(m.sourceFromFilename('한국 저평가 가치 투자주 (1).pdf'), 'InvestingPro 한국 저평가 가치');
assert.equal(m.sourceFromFilename('picks.pdf'), null);
assert.ok(m.SOURCE_PRESETS.includes('InvestingPro 한국 대장주 가치') && m.SOURCE_PRESETS.includes('InvestingPro 한국 저평가 가치'));
const options = m.monthOptionsHtml([{ month: '2026-11', pages: 1 }, { month: '2026-09', pages: 2 }, { month: null, pages: 3 }]);
assert.ok(options.includes('<option value="0">2026년 11월 · 1쪽') && options.includes('2026년 9월 · 2쪽') && options.includes('전체 · 3쪽'));
assert.equal(m.sectionDate({ month: '2026-11' }, '2026-11-01'), '2026-11-01');
assert.equal(m.sectionDate({ month: '2026-11' }, '2026-10-31'), '2026-10-31');
assert.equal(m.sectionDate({ month: null }, '2026-10-31'), '2026-10-31');
console.log('external_picks_render ok');
