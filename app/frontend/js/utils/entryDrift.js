// EINT-058 추천 이후 가격 변화: 현재가 기준으로 다시 계산한 기준 종가 대비 변화와 청산 참고선까지 거리.
// 현재가는 liveQuotes.js의 갱신 이벤트('livequotes')를 받아 쓴다(시세를 따로 조회하지 않는다).
// 사용: 마크업에 driftSlot(pick)을 넣고, startLiveQuotes(root) 전에 bindEntryDrift(root)를 한 번 부른다.
import { liveTicker } from './liveQuotes.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const num = (value) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
const pct = (value) => `${value > 0 ? '+' : ''}${value.toFixed(1)}%`;

export function driftSlot(pick) {
  const ticker = liveTicker(pick.symbol, pick.market);
  if (!ticker || !num(pick.close)) return '';
  const rules = pick.exit_plan?.rules || [];
  const middle = rules.find((rule) => rule.kind === 'target_line')?.price;
  const atr = pick.exit_plan?.atr;
  return `<p class="entry-drift" data-drift-ticker="${esc(ticker)}" data-close="${esc(pick.close)}" data-item="${esc(pick.item || pick.exit_plan?.item || '')}"
    ${num(middle) ? `data-middle="${esc(middle)}"` : ''} ${num(atr) ? `data-atr="${esc(atr)}"` : ''} hidden></p>`;
}

/** 현재가 price로 한 줄을 만든다. 데이터가 모자라면 빈 문자열. */
export function driftText({ close, price, middle, atr, item }) {
  if (!num(close) || !num(price) || Number(close) <= 0 || Number(price) <= 0) return '';
  const base = Number(close);
  const now = Number(price);
  const parts = [`기준 종가 대비 ${pct((now / base - 1) * 100)}${num(atr) && Number(atr) > 0 ? ` (${((now - base) / Number(atr)).toFixed(1)} ATR)` : ''}`];
  if (num(middle)) parts.push(`볼린저 중간선까지 ${pct((Number(middle) / now - 1) * 100)} (기준 종가 때 ${pct((Number(middle) / base - 1) * 100)})`);
  if (item === 'pullback_v4' && num(atr) && Number(atr) > 0) parts.push(`지금 사면 손절선(매수가 − 1.5×ATR)까지 ${pct((-1.5 * Number(atr) / now) * 100)}`);
  return parts.join(' · ');
}

export function bindEntryDrift(root) {
  if (root._entryDriftBound) return;
  root._entryDriftBound = true;
  root.addEventListener('livequotes', (event) => {
    const quotes = event.detail || {};
    root.querySelectorAll('[data-drift-ticker]').forEach((slot) => {
      const quote = quotes[slot.dataset.driftTicker];
      const text = quote?.status === 'ok' ? driftText({ close: slot.dataset.close, price: quote.price, middle: slot.dataset.middle,
        atr: slot.dataset.atr, item: slot.dataset.item }) : '';
      slot.hidden = !text;
      slot.innerHTML = text ? `<b>현재가 기준</b> ${esc(text)} <small>중간선은 기준일 값(매일 바뀜) · 목표가 아님</small>` : '';
    });
  });
}
