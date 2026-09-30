import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../app/frontend/js/views/kqsEngineViews.js', import.meta.url), 'utf8');
const { kqsStatusHtml: status, kqsReviewCandidatesHtml: candidates, kqsReviewPacketHtml: packet,
  kqsNativeReconciliationHtml: reconciliation } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const trace = { source: 'snapshot', available: true, read_only: true, as_of: '2026-09-28',
  generated_at: '2026-09-29T00:00:00+00:00', expires_at: '2026-09-29T02:00:00+00:00',
  snapshot_id: 'snap-1', engine: { package_version: '0.6.0', code_sha256: 'code-1', dataset_hash: 'data-1', run_id: 'run-1' } };
const zero = status({ ...trace, status: 'no_recommendations', selected_entry_count: 0 });
for (const value of ['추천 0건(정상 결과)', '별도 점검 필요', '자동 모의 포지션: 미사용', 'snap-1', '0.6.0']) assert.ok(zero.includes(value), value);
assert.ok(!zero.includes('Ubuntu-26.04'));
assert.ok(!zero.includes('최근 실행'));
const reason = { code: 'rsi', label: 'RSI', evidence: 'RSI(14) 20 ≤ 30 <img src=x onerror=alert(1)>' };
const candidate = { symbol: '000001', name: '<script>evil()</script>', market: 'KOSPI', signal_type: 'swing',
  signal_id: 'signal-1', execution_timing: 'next_trading_session', selection_rank: 1, market_cap_rank: 10,
  reasons: [reason], review: { status: 'deferred', note: '<svg onload=evil()>' } };
const queue = candidates({ ...trace, latest_selected_count: 1, candidates: [candidate] });
for (const value of ['RSI(14) 20 ≤ 30', '미제공', 'data-kqs-packet', 'data-kqs-review-status', 'snap-1']) assert.ok(queue.includes(value), value);
assert.ok(!queue.includes('<script>'));
assert.ok(!queue.includes('<img'));
assert.ok(!queue.includes('<svg'));
const detail = packet({ ...trace, found: true, candidate, selection_evidence: { reasons: [reason], features: { rsi14: 20 },
  exit_reference: 'RSI ≥ 50 (자동 매도하지 않음)', selection_note: '수익 예측 순위가 아닙니다.' } });
for (const value of ['게시 당시 추천 이유', 'rsi14: 20', 'code-1', 'data-1', 'run-1', 'snap-1', '만료', '자동 매도하지 않음']) assert.ok(detail.includes(value), value);
assert.ok(!detail.includes('<img'));
for (const render of [status, candidates, packet]) {
  const error = render({ available: false, detail: '<script>missing/expired</script>' });
  assert.ok(!error.includes('<script>'));
  assert.ok(!error.includes('data-kqs-review-status'));
}
const empty = candidates({ ...trace, latest_selected_count: 0, candidates: [] });
assert.ok(empty.includes('추천 0건은 정상 결과'));
const unused = reconciliation({ applicability: 'not_applicable', reason: '추천만 게시 <script>' });
assert.ok(unused.includes('미사용'));
assert.ok(!unused.includes('<script>'));
assert.ok(!unused.includes('stg-kqs-snapshot-save'));
const main = await readFile(new URL('../../app/frontend/js/views/strategyList.js', import.meta.url), 'utf8');
assert.ok(main.includes("from './kqsEngineViews.js'"));
assert.ok(main.includes("data.applicability === 'not_applicable'"));
console.log('KQS native views: status/zero/errors/reasons/trace/escaping/not-applicable wiring passed');
