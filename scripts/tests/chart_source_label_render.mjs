// 일봉 차트 출처 표기: 마지막 봉 날짜, 갱신 지연 경고, 시뮬레이션.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../app/frontend/js/utils/chartSourceLabel.js', import.meta.url), 'utf8');
const { chartSourceLabel } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

assert.deepEqual(chartSourceLabel({ last_bar_date: '2026-10-06', stale: false }), { text: 'Yahoo Finance · 10/06 일봉 · 최대 15분 지연', stale: false });
assert.deepEqual(chartSourceLabel({ last_bar_date: '2026-09-10', stale: true }), { text: '마지막 봉 09/10 · 시세 갱신 지연', stale: true });
assert.equal(chartSourceLabel({ is_simulated: true }).text, '시뮬레이션 데이터');
assert.equal(chartSourceLabel({}).text, 'Yahoo Finance · 최대 15분 지연');
console.log('chart_source_label_render ok');
