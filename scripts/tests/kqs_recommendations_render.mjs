import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const path = new URL('../../app/frontend/js/views/kqsRecommendations.js', import.meta.url);
const source = await readFile(path, 'utf8');
const { kqsRecommendationsHtml: render } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const error = render({ detail: '<script>alert(1)</script>' });
assert.ok(!error.includes('<script>'));
assert.ok(!error.includes('data-kqs-decision-form'));
const empty = render({ read_only: true, signals: [], engine: {}, selected_entry_count: 0 });
assert.ok(empty.includes('유효한 추천 신호가 없습니다'));
const html = render({
  read_only: true, snapshot_id: 'snapshot-1', publication: { calendar: { calendar_id: 'krx-equity-2026', data_available_time_kst: '06:00', calendar_sha256: 'calendar' } }, engine: { package_version: '0.3.0', code_sha256: 'code' },
  signals: [{ signal_id: 'id1', action: 'entry', selection_rank: 1, name: '<img src=x>',
    symbol: '005930', market: 'KOSPI', signal_type: 'swing',
    reasons: [{ code: 'rsi', label: 'RSI', evidence: 'RSI14 20 ≤ 30' }],
    exit_reference: 'RSI ≥ 50', selection_note: '수익 예측 순위가 아닙니다.' }],
});
assert.ok(!html.includes('<img'));
for (const text of ['버전 0.3.0', '게시 snapshot-1', 'RSI14 20 ≤ 30', 'RSI ≥ 50', 'krx-equity-2026', '공개 기대 06:00 KST', 'data-kqs-decision-form']) {
  assert.ok(html.includes(text), text);
}
console.log('KQS rendering: error/zero/reason/reference/version/snapshot/manual-form escaping passed');
