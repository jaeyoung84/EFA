// 추천 종목의 슈퍼트렌드 참고 칩(EINT-054). 조건이 아니라 참고 정보다: 추천을 막거나 순서를 바꾸지 않는다.
// 사용: 마크업에 supertrendSlot(symbol, market)을 넣고 그린 뒤 fillSupertrendRef(root). 칩을 누르면 슈퍼트렌드 차트로 이동.
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const CACHE_MS = 5 * 60 * 1000;
// supertrendChart.js가 읽는 마지막 종목·설정 저장 키와 같아야 한다.
const CHART_PREF_KEY = 'efa-supertrend-chart';
const cache = new Map();

export function krTicker(symbol, market = '') {
  const code = String(symbol || '').trim().toUpperCase();
  if (/\.(KS|KQ)$/.test(code)) return code;
  return /^[0-9A-Z]{6}$/.test(code) ? `${code}.${/KOSDAQ|코스닥/i.test(market) ? 'KQ' : 'KS'}` : '';
}

export function supertrendSlot(symbol, market, name = '') {
  const ticker = krTicker(symbol, market);
  return ticker ? `<span class="pick-ctx st-ref is-loading" data-st-ref="${esc(ticker)}" data-st-name="${esc(name)}"></span>` : '';
}

function lookup(tickers) {
  const now = Date.now();
  const missing = tickers.filter((ticker) => !(cache.has(ticker) && now - cache.get(ticker).at < CACHE_MS)).slice(0, 30);
  if (missing.length) {
    const batch = fetch(`/api/chart/supertrend-reference?tickers=${encodeURIComponent(missing.join(','))}`)
      .then((response) => (response.ok ? response.json() : { items: {} })).then((data) => data.items || {}).catch(() => ({}));
    missing.forEach((ticker) => cache.set(ticker, { at: now, promise: batch.then((items) => {
      if (!items[ticker] || items[ticker].status !== 'ok') cache.delete(ticker);   // 실패는 다음에 다시 묻는다
      return items[ticker];
    }) }));
  }
  return Promise.all(tickers.map((ticker) => cache.get(ticker)?.promise));
}

const dirText = (direction) => (direction === -1 ? '▲상승' : direction === 1 ? '▼하락' : '계산 전');
const shortDate = (iso) => (iso ? `${iso.slice(5, 7)}/${iso.slice(8, 10)}` : '');

export function supertrendRefHtml(item) {
  if (!item || item.status !== 'ok') {
    return `<span class="pick-ctx-chip is-muted">슈퍼트렌드 ${item?.status === 'insufficient' ? '이력 부족' : '조회 불가'}</span>`;
  }
  const flip = item.last_flip_type
    ? `${item.last_flip_type === 'up' ? '상승' : '하락'} 전환 ${item.flip_today ? '오늘' : `${item.days_since_flip}일째`}`
    : '전환 기록 없음';
  const weekly = item.weekly_dir === null || item.weekly_dir === undefined ? '' : ` · 주봉 ${dirText(item.weekly_dir)}`;
  const tone = item.dir === -1 ? 'is-st-up' : 'is-st-down';
  const detail = [
    `${item.as_of} 확정 종가 기준 (ATR 10 × 3)`,
    `일봉 ${dirText(item.dir)}, 직전 확정 주봉 ${dirText(item.weekly_dir)}${item.weekly_ref_end ? `(${item.weekly_ref_end} 주)` : ''}`,
    item.last_flip_date ? `최근 확정 전환 ${item.last_flip_date} ${item.last_flip_type === 'up' ? 'BUY·상승' : 'SELL·하락'}` : '확정 전환 없음',
    item.distance_pct !== null ? `추세선 ${Number(item.st).toLocaleString('ko-KR')}까지 ${Number(item.distance_pct).toFixed(1)}% (${Number(item.atr_distance).toFixed(1)} ATR)` : '',
    item.flip_count !== null ? `최근 20거래일 전환 ${item.flip_count}회` : '',
    item.adx !== null ? `ADX14 ${Number(item.adx).toFixed(1)}` : '',
    item.intraday_break ? '오늘 장중 전일 상승선 아래(미확정)' : '',
    '참고 정보이며 추천 규칙·순서에 반영되지 않습니다. 누르면 슈퍼트렌드 차트로 이동합니다.',
  ].filter(Boolean).join('\n');
  return `<button type="button" class="pick-ctx-chip st-ref-chip ${tone}" title="${esc(detail)}" data-st-open>ST 일봉 ${dirText(item.dir)}${weekly} · ${esc(flip)}${item.distance_pct !== null ? ` · 선까지 ${Number(item.distance_pct).toFixed(1)}%` : ''}${item.intraday_break ? ' · ⚠장중 이탈' : ''} <small>${esc(shortDate(item.as_of))}</small></button>`;
}

export async function fillSupertrendRef(root) {
  const slots = [...root.querySelectorAll('[data-st-ref]')];
  if (!slots.length) return;
  const tickers = [...new Set(slots.map((slot) => slot.dataset.stRef))];
  const items = await lookup(tickers);
  const byTicker = Object.fromEntries(tickers.map((ticker, i) => [ticker, items[i]]));
  slots.forEach((slot) => {
    if (!slot.isConnected) return;
    slot.classList.remove('is-loading');
    slot.innerHTML = supertrendRefHtml(byTicker[slot.dataset.stRef]);
    slot.querySelector('[data-st-open]')?.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      try {
        const prefs = JSON.parse(localStorage.getItem(CHART_PREF_KEY) || '{}');
        localStorage.setItem(CHART_PREF_KEY, JSON.stringify({ ...prefs, ticker: slot.dataset.stRef, name: slot.dataset.stName || slot.dataset.stRef, atr: 10, factor: 3 }));
      } catch { /* 저장 불가 브라우저: 차트는 마지막 종목으로 열린다 */ }
      window.navigate('supertrend-chart');
    });
  });
}
