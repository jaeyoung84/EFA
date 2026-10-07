// 추천 판단 근거 칩: 갭 경고·업종 집중·중립 공시 표시, 특이사항 없음, escape.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = (await readFile(new URL('../../app/frontend/js/utils/pickContext.js', import.meta.url), 'utf8'))
  .replace(/^import \{ authRequest \} from '..\/auth.js';$/m, 'const authRequest = async () => null;');
const m = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const full = m.contextHtml({
  gap: { status: 'warning', gap_pct: -6.0, as_of: '2026-10-06', open: 94, previous_close: 100, previous_date: '2026-10-02' },
  sector: '자동차', same_sector: [{ symbol: '000270', name: '<b>기아</b>' }],
  recent_activity: { count: 2, kinds: { 자사주: 1 }, latest_date: '2026-10-06' },
});
assert.ok(full.includes('⚠ 기준일 갭하락 -6.00%'));
assert.ok(full.includes('같은 업종 추천 2종목 · 자동차'));
assert.ok(full.includes('최근 지분·자사주 공시 2건 (10/06)'));
assert.ok(!full.includes('<b>기아</b>') && !full.includes('악재'));
assert.ok(m.contextHtml({ gap: { status: 'ok', gap_pct: -1 }, same_sector: [], recent_activity: null }).includes('특이사항 없음'));
assert.ok(m.contextHtml({ gap: { status: 'unavailable' }, same_sector: [] }).includes('기준일 시세 확인 불가'));
console.log('pick_context_render ok');
