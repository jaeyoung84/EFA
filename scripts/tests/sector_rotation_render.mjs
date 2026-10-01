// EINT-021 섹터 순환 화면: 축 범위, 이름표 겹침 해소, 주도권 요약, 표·그림 escape.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../app/frontend/js/views/sectorRotation.js', import.meta.url), 'utf8');
const m = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const trail = (pairs) => pairs.map(([x, y], i) => ({ week: `2026-09-${String(i + 1).padStart(2, '0')}`, rs_ratio: x, rs_momentum: y }));
const data = {
  available: true, latest_week: '2026-09-30', benchmark: { name: 'KODEX 200' }, smoothing_weeks: { short: 4, long: 13, momentum: 4 },
  disclaimer: '<b>참고</b>',
  sectors: [
    { key: 'semis', name: '반도체', etf_name: 'KODEX 반도체', symbol: '091160', quadrant: 'leading', quadrant_4w_ago: 'improving',
      rs_ratio: 103, rs_momentum: 102, relative_4w_pct: 11.3, relative_12w_pct: 7.3, return_12w_pct: 20, trail: trail([[99, 101], [101, 102], [103, 102]]) },
    { key: 'banks', name: '<img src=x>', etf_name: 'KODEX 은행', symbol: '091170', quadrant: 'weakening', quadrant_4w_ago: 'leading',
      rs_ratio: 100.7, rs_momentum: 98.9, relative_4w_pct: -5.6, relative_12w_pct: 7.8, return_12w_pct: 9, trail: trail([[102, 101], [101.5, 99.5], [100.7, 98.9]]) },
    { key: 'net', name: '인터넷', etf_name: 'TIGER 인터넷', symbol: '365000', quadrant: 'improving', quadrant_4w_ago: 'improving',
      rs_ratio: 95.4, rs_momentum: 100.6, relative_4w_pct: -1.9, relative_12w_pct: 5.7, return_12w_pct: 4, trail: trail([[94, 99], [95, 100], [95.4, 100.6]]) },
  ],
};

const domain = m.rrgDomain(data.sectors, 3);
assert.ok(domain.x[0] < 94 && domain.x[1] > 103 && Math.abs((domain.x[0] + domain.x[1]) / 2 - 100) < 1e-9);  // 100 가운데

const placed = m.placeLabels([{ key: 'a', x: 100, y: 50, width: 40 }, { key: 'b', x: 105, y: 55, width: 40 }, { key: 'c', x: 600, y: 52, width: 40 }]);
assert.equal(placed[0].y, 50); assert.equal(placed[1].y, 66); assert.equal(placed[2].y, 52);  // 겹치는 b만 아래로

const sum = m.rotationSummary(data.sectors);
assert.deepEqual(sum.leading.map((s) => s.key), ['semis']);
assert.deepEqual(sum.fading.map((s) => s.key), ['banks']);
assert.deepEqual(sum.rising.map((s) => s.key), ['net']);
const summary = m.summaryHtml(data);
assert.ok(summary.includes('지금 시장을 이끄는 섹터는 <b>반도체</b>') && summary.includes('개선 → 주도') && summary.includes('주도 → 약화'));
assert.ok(!summary.includes('<img src=x>'));

const svg = m.rrgSvg(data, { tail: 3, hidden: new Set(['net']) });
assert.ok(svg.startsWith('<svg') && svg.includes('주도') && svg.includes('data-rrg-key="semis"') && !svg.includes('data-rrg-key="net"'));
assert.ok(!svg.includes('<img src=x>') && (svg.match(/marker-end="url\(#rrg-arrow-/g) || []).length === 2);

const table = m.tableHtml(data);
assert.ok(table.indexOf('반도체') < table.indexOf('인터넷') && table.includes('+11.3%') && !table.includes('<img src=x>'));
console.log('Sector rotation: domain, label nudging, summary, svg, table, escaping passed');
