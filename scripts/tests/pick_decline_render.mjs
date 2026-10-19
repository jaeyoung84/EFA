// EINT-058: 하락 구간 공시·뉴스 칩(없음/실패/일부만 확인/실적 태그/escape)과 '현재가 기준' 한 줄 계산.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const load = async (file, stubs) => {
  const source = (await readFile(new URL(`../../app/frontend/js/utils/${file}`, import.meta.url), 'utf8')).replace(/^import .*$/gm, '');
  return import(`data:text/javascript;base64,${Buffer.from(stubs + source).toString('base64')}`);
};
const d = await load('pickDecline.js', 'const authRequest = async () => ({});\n');
const base = { status: 'ok', start: '2026-10-12', end: '2026-10-16', return_pct: -12, worst_day: { date: '2026-10-13', pct: -6.25 } };
const quiet = d.declineHtml({ ...base, disclosures: [], news: [], news_partial: false });
assert.ok(quiet.includes('하락 구간 10/12~10/16 -12.0%') && quiet.includes('공시·뉴스 없음') && quiet.includes('is-muted') && quiet.includes('Chan 2003'));
const busy = d.declineHtml({ ...base, disclosures: [{ date: '2026-10-13', title: '영업(잠정)실적<b>', tag: '실적' }], news: [{ date: '2026-10-13', title: '실적 부진', source: 'A' }], news_partial: true });
assert.ok(busy.includes('공시 1(실적)') && busy.includes('뉴스 1+(일부만 확인)') && busy.includes('is-caution') && busy.includes('&lt;b&gt;') && !busy.includes('<b>'));
const failed = d.declineHtml({ ...base, disclosures: null, news: [], disclosure_error: 'DART 조회 실패: x' });
assert.ok(failed.includes('공시 확인 불가') && !failed.includes('공시·뉴스 없음'));
assert.ok(d.declineHtml({ status: 'unavailable', reason: '봉 부족' }).includes('확인 불가'));

const e = await load('entryDrift.js', "const liveTicker = (s, m) => (/^[0-9A-Z]{6}$/.test(s) ? `${s}.${m === 'KOSDAQ' ? 'KQ' : 'KS'}` : '');\n");
const swing = e.driftText({ close: 50000, price: 51500, middle: 52000, item: 'swing' });
assert.ok(swing.includes('기준 종가 대비 +3.0%') && swing.includes('볼린저 중간선까지 +1.0%') && swing.includes('기준 종가 때 +4.0%'));
const v4 = e.driftText({ close: 100, price: 102, atr: 4, item: 'pullback_v4' });
assert.ok(v4.includes('+2.0% (0.5 ATR)') && v4.includes('손절선(매수가 − 1.5×ATR)까지 -5.9%'));
assert.equal(e.driftText({ close: 100, price: null }), '');
const slot = e.driftSlot({ symbol: '005930', market: 'KOSPI', close: 50000, item: 'swing', exit_plan: { rules: [{ kind: 'target_line', price: 52000 }] } });
assert.ok(slot.includes('data-drift-ticker="005930.KS"') && slot.includes('data-middle="52000"') && slot.includes('hidden'));
assert.equal(e.driftSlot({ symbol: 'AAPL', market: 'NASDAQ', close: 1 }), '');
console.log('pick_decline_render ok');
