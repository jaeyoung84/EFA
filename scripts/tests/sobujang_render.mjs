// EINT-049 소부장 전체: 상승·하락 요약(테마별 중간값), escape, 요약 없는 응답은 빈 문자열.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../app/frontend/js/views/todayGainers.js', import.meta.url), 'utf8');
const m = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const html = m.summaryMarkup({ summary: { up: 151, down: 18, flat: 4, median_change_pct: 3.15,
  by_theme: { '반도체 장비': { count: 97, median_change_pct: 3.24 }, '<b>x</b>': { count: 1, median_change_pct: -1.5 } } } });
assert.ok(html.includes('상승 <b class="is-up">151</b>') && html.includes('+3.15%') && html.includes('반도체 장비 97종목') && html.includes('-1.50%'));
assert.ok(!html.includes('<b>x</b>') && html.includes('&lt;b&gt;x'));
assert.equal(m.summaryMarkup({}), '');
console.log('sobujang_render ok');
