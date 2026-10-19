// 추천 종목 슈퍼트렌드 참고 칩(EINT-054): 티커 변환, 방향·주봉·전환·거리 문구, 실패·이력 부족, escape.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../app/frontend/js/utils/supertrendRef.js', import.meta.url), 'utf8');
const m = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

assert.equal(m.krTicker('005930', 'KOSPI'), '005930.KS');
assert.equal(m.krTicker('247540', 'KOSDAQ'), '247540.KQ');
assert.equal(m.krTicker('AAPL', ''), '');
assert.equal(m.supertrendSlot('AAPL', 'NASDAQ'), '');
assert.ok(m.supertrendSlot('005930', 'KOSPI', '<삼성>').includes('data-st-ref="005930.KS"') && m.supertrendSlot('005930', 'KOSPI', '<삼성>').includes('&lt;삼성&gt;'));

const up = m.supertrendRefHtml({ status: 'ok', as_of: '2026-10-16', dir: -1, weekly_dir: 1, weekly_ref_end: '2026-10-10', flip_today: null,
  last_flip_type: 'up', last_flip_date: '2026-10-13', days_since_flip: 3, distance_pct: 4.24, atr_distance: 1.3, st: 70000, flip_count: 1, adx: 22.5, intraday_break: false });
assert.ok(up.includes('ST 일봉 ▲상승') && up.includes('주봉 ▼하락') && up.includes('상승 전환 3일째') && up.includes('선까지 4.2%') && up.includes('is-st-up'));
assert.ok(up.includes('추천 규칙·순서에 반영되지 않습니다') && up.includes('10/16'));

const today = m.supertrendRefHtml({ status: 'ok', as_of: '2026-10-16', dir: 1, weekly_dir: null, flip_today: 'down', last_flip_type: 'down',
  days_since_flip: 0, distance_pct: 2, atr_distance: 0.5, st: 1, flip_count: 3, adx: null, intraday_break: true });
assert.ok(today.includes('하락 전환 오늘') && !today.includes('· 주봉') && today.includes('⚠장중 이탈') && today.includes('is-st-down'));

assert.ok(m.supertrendRefHtml(undefined).includes('조회 불가'));
assert.ok(m.supertrendRefHtml({ status: 'insufficient' }).includes('이력 부족'));

// EINT-055 보유종목 전환 알림: 전환한 종목만, 현금·형식 오류 제외, 조회 실패 수, 전환 없는 날은 배너 없음.
const calls = [];
globalThis.fetch = async (url) => {
  calls.push(url);
  return { ok: true, json: async () => ({ items: {
    '005930.KS': { ticker: '005930.KS', status: 'ok', as_of: '2026-10-16', dir: 1, weekly_dir: -1, flip_today: 'down', distance_pct: 3.1 },
    'AAPL': { ticker: 'AAPL', status: 'ok', as_of: '2026-10-16', dir: -1, weekly_dir: -1, flip_today: null, distance_pct: 5 },
    '000660.KS': { ticker: '000660.KS', status: 'unavailable' },
  } }) };
};
const flips = await m.holdingFlips([{ ticker: 'CASH', name: '현금' }, { ticker: '005930.KS', name: '삼성<전자>' }, { ticker: 'AAPL', name: 'Apple' },
  { ticker: '000660.KS', name: 'SK하이닉스' }, { ticker: 'bad ticker', name: 'x' }]);
assert.equal(calls.length, 1);
assert.ok(!decodeURIComponent(calls[0]).includes('CASH') && !decodeURIComponent(calls[0]).includes('bad'));
assert.equal(flips.checked, 3); assert.equal(flips.failed, 1);
assert.deepEqual(flips.items.map((item) => item.ticker), ['005930.KS']);
const tab = m.holdingFlipsHtml(flips);
assert.ok(tab.includes('삼성&lt;전자&gt;') && tab.includes('▼ 하락 전환') && tab.includes('2026-10-16 종가 확정') && tab.includes('1종목은 시세 조회 불가'));
assert.ok(m.holdingFlipsBannerHtml(flips).includes('오늘 슈퍼트렌드 전환') && m.holdingFlipsBannerHtml(flips).includes('data-st-chart="005930.KS"'));
const quiet = { checked: 3, items: [], failed: 0 };
assert.equal(m.holdingFlipsBannerHtml(quiet), '');
assert.ok(m.holdingFlipsHtml(quiet).includes('오늘 추세가 바뀐 보유종목이 없습니다'));
assert.ok(m.holdingFlipsHtml(null).includes('확인하는 중'));
assert.ok(m.holdingFlipsHtml({ checked: 0, items: [], failed: 0 }).includes('보유 종목이 없습니다'));
console.log('supertrend_ref_render ok');
