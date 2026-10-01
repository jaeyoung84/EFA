import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const path = new URL('../../app/frontend/js/views/recommendationOutcomes.js', import.meta.url);
const source = await readFile(path, 'utf8');
const { outcomesHtml: render } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const summary = (count, gate) => ({ observed_count: count, mean_return_pct: 1.5, median_return_pct: -0.5, win_rate_pct: 60,
  mean_excess_return_pct: null, sample_gate: gate });
const html = render({
  available: true, through: '2026-10-30', source_count: 21, row_count: 2, horizons: [1, 5, 10, 20], min_sample: 20,
  items: [
    { item: 'swing', label: 'Bollinger-RSI', title: '과매도', pick_count: 25, session_count: 9,
      horizons: { 1: summary(25, 'ready'), 5: summary(3, 'insufficient_sample') } },
    { item: 'long_term', label: 'RSI-OBV', title: '장기', pick_count: 0, session_count: 0, horizons: {} },
    { item: 'pullback_v4', label: 'Trend Pullback v4', title: '눌림목', pick_count: 0, session_count: 0, horizons: {} }],
  rows: [
    { as_of: '2026-10-29', item: 'pullback_v4', rank: 1, name: '<img src=x onerror=alert(1)>', symbol: '010170', market: 'KOSDAQ',
      entry_open: 1000, entry_date: '2026-10-30', status: 'observed', horizons: { 1: { observed: true, return_pct: 2.5 }, 5: { observed: false } } },
    { as_of: '2026-10-30', item: 'swing', rank: 2, name: 'B', symbol: '005930', market: 'KOSPI', entry_open: null,
      status: 'awaiting_entry', horizons: {} }],
  disclaimer: '<b>d</b>',
});
assert.equal((html.match(/class="ro-item"/g) || []).length, 3);
assert.ok(!html.includes('<img src=x') && !html.includes('<b>d</b>'));
assert.ok(html.includes('판단 가능') && html.includes('표본 부족 3/20'));
assert.ok(html.includes('class="ro-up">+2.50%') && html.includes('class="ro-down">-0.50%'));
assert.ok(html.includes('매수일 대기') && html.includes('>대기<'));
const down = render({ available: false, detail: '<i>없음</i>' });
assert.ok(down.includes('&lt;i&gt;없음') && !down.includes('<i>없음'));
console.log('Recommendation outcomes: item gates, returns coloring, pending rows, escaping passed');
