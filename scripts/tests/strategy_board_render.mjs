import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const path = new URL('../../app/frontend/js/views/strategyBoard.js', import.meta.url);
const source = (await readFile(path, 'utf8')).replace(/^import .*$/gm, '').replace(/^/,
  'const authRequest = async () => ({}); const isAuthenticated = () => true; const openAuthModal = () => {}; const manualRequest = (f, b) => b; const sectorSlot = (s) => `<span data-sector-symbol="${s}"></span>`; const fillSectorBadges = () => {}; const liveSlot = (s, m) => `<span data-live-ticker="${s}.${m}"></span>`; const startLiveQuotes = () => {}; const riskSlot = (s, o) => `<span data-risk-symbol="${s}"${o && o.full ? " data-risk-full" : ""}></span>`; const fillRiskBadges = () => {}; const exitPlanHtml = (plan) => (plan ? `<div class="exit-plan"></div>` : ""); const contextSlot = (s) => `<span data-ctx-symbol="${s}"></span>`; const fillPickContext = () => {}; const supertrendSlot = (s) => `<span data-st-ref="${s}"></span>`; const fillSupertrendRef = () => {}; const declineSlot = (s) => `<span data-decline-symbol="${s}"></span>`; const fillPickDecline = () => {}; const driftSlot = () => ""; const bindEntryDrift = () => {}; const funnelHtml = () => "";\n');
const { boardHtml: render } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const item = (key, picks, published = true) => ({ item: key, label: key, title: key, summary: 's', order: 'o', exit: 'e',
  max_picks: 5, published, picks });
const html = render({
  available: true, as_of: '2026-09-30', expires_at: '2026-10-01T00:00:00+00:00', package_version: '0.7.0',
  items: [item('swing', [{ rank: 1, name: '<img src=x onerror=alert(1)>', symbol: '005930', market: 'KOSPI', close: 70000,
    reasons: ['<b>r</b>'], exit_reference: 'x' }]), item('long_term', []), item('pullback_v4', [], false)],
  sell_signals: [
    { state: 'exit_signal', item: 'pullback_v4', symbol: '000660', name: 'B', bought_on: '2026-09-20', fill_price: 100,
      remaining_quantity: 3, close: 95, reason_label: '손절선 이하', stop_price: 96, trailing_level: 97, holding_days: 7,
      holding_limit: 15, market_regime_off: false },
    { state: 'no_item', item: null, symbol: '035420', bought_on: '2026-09-01', fill_price: 1, remaining_quantity: 1,
      detail: '<script>x</script>' }],
  disclaimer: 'd',
});
assert.equal((html.match(/class="sb-item"/g) || []).length, 3);
assert.ok(!html.includes('<img src=x') && !html.includes('<script>') && !html.includes('<b>r</b>'));
assert.ok(html.includes('조건을 만족한 종목이 없습니다'));
assert.ok(html.includes('엔진 v2 게시 후부터'));
assert.ok(html.includes('매도 신호') && html.includes('손절 96'));
assert.ok(html.includes('class="sb-count is-alert">1건'));
const down = render({ available: false, detail: '<i>gone</i>', items: [], sell_signals: [], disclaimer: '' });
assert.ok(down.includes('검증된 추천을 사용할 수 없습니다') && !down.includes('<i>gone</i>'));
// Codex 수정: CODX-EXIT-001 (2026-10-12) — 가격선 경고와 확정 관측 배지를 구분한다.
const warning = render({ available: true, items: [], sell_signals: [
  { state: 'exit_signal', signal_basis: 'close_warning', item: 'pullback_v4', symbol: '000660',
    bought_on: '2026-10-02', fill_price: 100, remaining_quantity: 1, close: 95,
    reason_label: '손절선 이하', stop_price: 97, trailing_level: 98, holding_days: 1, holding_limit: 15 },
], disclaimer: '' });
assert.ok(warning.includes('가격선 주의') && warning.includes('다음 시가 미확정'));
assert.ok(warning.includes('class="sb-count ">0건'));
assert.ok(!warning.includes('<tr class="is-alert">'));
// EINT-020: 카드의 판단 버튼·내 기록 배지·엔진 경고 배너
const withDecisions = render({
  available: true, as_of: '2026-09-30', items: [item('swing', [{ rank: 1, signal_id: 'sig-1', name: 'A', symbol: '005930', market: 'KOSPI', reasons: [] }])],
  my_decisions: { 'sig-1': [{ decision: 'buy', quantity: 3, fill_price: 70000 }, { decision: 'hold' }] },
  sell_signals: [], disclaimer: '',
  health: { level: 'error', alerts: [{ level: 'error', title: '오늘 추천이 게시되지 않았습니다', message: '<b>x</b>' }] },
});
assert.ok(withDecisions.includes('data-sb-decision data-signal-id="sig-1"'));
assert.ok(withDecisions.includes('매수 3주 @70,000') && withDecisions.includes('>관망</span>'));
assert.ok(withDecisions.includes('data-sb-health="error"') && withDecisions.includes('오늘 추천이 게시되지 않았습니다'));
assert.ok(!withDecisions.includes('<b>x</b>'));
const healthy = render({ available: true, as_of: '2026-09-30', items: [], sell_signals: [], disclaimer: '',
  health: { level: 'ok', alerts: [], last_run_finished_at: '2026-10-01T00:00:00+00:00' } });
assert.ok(!healthy.includes('data-sb-health') && healthy.includes('엔진 정상'));
// Codex 수정: CODX-EXIT-001 (2026-10-12) — 대시보드도 종가 경고를 별도 표시한다.
const dashboardSource = (await readFile(new URL('../../app/frontend/js/views/dashboard.js', import.meta.url), 'utf8'))
  .replace(/^import .*$/gm, '');
const { briefingHtml } = await import(`data:text/javascript;base64,${Buffer.from(dashboardSource).toString('base64')}`);
const dashboardWarning = briefingHtml('sells', { loggedIn: true, board: { sell_signals: [
  { state: 'exit_signal', signal_basis: 'close_warning', item: 'pullback_v4', symbol: '000660',
    bought_on: '2026-10-02', fill_price: 100, remaining_quantity: 1, close: 95, reason_label: '손절선 이하' },
] } });
assert.ok(dashboardWarning.includes('가격선 주의') && dashboardWarning.includes('다음 시가 미확정'));
assert.ok(!dashboardWarning.includes('<span class="dash-badge is-sell">매도 신호</span>'));
console.log('Strategy board and dashboard: exit warning rendering passed');
