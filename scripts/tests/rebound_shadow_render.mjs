// 사전등록 B-1 관측 칸: 판정 전 표시, 짝 결과, 미진입 0, escape.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../app/frontend/js/views/recommendationOutcomes.js', import.meta.url), 'utf8');
const m = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const html = m.reboundShadowHtml({ available: true, through: '2026-10-20', registered_from: '2026-10-07',
  preregistration: 'docs/engine/PREREGISTRATION_B_rebound_confirmation_v1.md', preregistration_sha256: 'cb3b3ddbe301d43a',
  summary: { signals: 2, closed_pairs: 1, target_closed_pairs: 100, b_entry_rate_pct: 0, mean_net_a_pct: 1.5, mean_diff_pct: -1.5, verdict: 'not_due' },
  rows: [{ as_of: '2026-10-08', name: '<b>x</b>', symbol: '000001', market: 'KOSPI', confirmation: { confirmed: false },
    arm_a: { status: 'closed', net_return_pct: 1.5 }, arm_b: { status: 'not_entered', net_return_pct: 0 }, status: 'closed_pair' },
  { as_of: '2026-10-09', name: 'y', symbol: '000002', market: 'KOSDAQ', confirmation: null, arm_a: null, arm_b: null, status: 'awaiting_s1' }],
  disclaimer: '판정하지 않습니다.' });
assert.ok(html.includes('판정 전') && html.includes('1 / 100'));
assert.ok(html.includes('확인 안 됨') === false && html.includes('아님') && html.includes('다음 거래일 대기'));
assert.ok(html.includes('+1.50%') && html.includes('-1.50%') && !html.includes('<b>x</b>'));
assert.ok(m.reboundShadowHtml({ available: false, detail: '파일 없음' }).includes('파일 없음'));
const passed = m.reboundShadowHtml({ available: true, summary: { verdict: 'PASS', ci95: [0.1, 0.9] }, rows: [] });
assert.ok(passed.includes('규칙 변경 제안 가능') && passed.includes('95% 구간'));
console.log('rebound_shadow_render ok');
