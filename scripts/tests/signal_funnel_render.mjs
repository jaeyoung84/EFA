// 평균회귀 신호 단계·관찰 후보 패널: 단계 숫자, 연속 0건 안내, 미충족 조건, '추천 아님', escape.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../app/frontend/js/utils/signalFunnel.js', import.meta.url), 'utf8');
const { funnelHtml } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const zero = { swing: { new_entry: 0 } };
const html = funnelHtml({ available: true, as_of: '2026-10-06',
  today: { universe: 2915, eligible: 276, stages: { low_liquidity: 2241, outside_market_cap_tier: 133 },
    swing: { eligible: 276, below_band: 6, rsi_le_30: 3, both: 0, new_entry: 0, selected: 0, rsi_le_35: 14 },
    long_term: { eligible: 276, rsi_cross_30: 1, obv_rising: 120, both: 0, new_entry: 0, selected: 0 } },
  watch: { swing: [{ name: '<b>x</b>', symbol: '000001', market: 'KOSPI', band_gap_pct: 1.2, rsi: 31.5, missing: ['볼린저 하단 이하', 'RSI ≤ 30'] }],
    long_term: [{ name: 'y', symbol: '000002', market: 'KOSDAQ', previous_rsi: 29, rsi: 31, obv_rising: false, missing: ['OBV 9일 평균 위·증가'] }] },
  watch_rules: { swing: '하단 +3% 이내', long_term: 'RSI 돌파' },
  history: [{ as_of: 'a', ...zero }, { as_of: 'b', ...zero }, { as_of: 'c', ...zero }, { as_of: 'd', ...zero }],
  disclaimer: '추천이 아닙니다.' });
assert.ok(html.includes('추천 아님') && html.includes('기본 적격 <b>276</b>'));
assert.ok(html.includes('거래대금 50억 미만 2,241'));
assert.ok(html.includes('<span>종가 ≤ 볼린저 하단</span><b>6</b>') && html.includes('<span>두 조건 동시</span><b>0</b>'));
assert.ok(html.includes('4거래일 연속 새 신호 0건'));
assert.ok(html.includes('미충족: 볼린저 하단 이하, RSI ≤ 30') && html.includes('RSI 29→31'));
assert.ok(!html.includes('<b>x</b>'));
assert.ok(funnelHtml({ available: false, detail: '아직 없음' }).includes('아직 없음'));
console.log('signal_funnel_render ok');
