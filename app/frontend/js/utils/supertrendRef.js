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

// ── 보유종목 추세 전환 알림(EINT-055): 마지막 확정 일봉에서 방향이 바뀐 종목만 돌려준다 ──
const HOLDING_TICKER = /^[0-9A-Z.^=-]{1,24}$/;

/** positions: [{ticker, name}] → 오늘(마지막 확정 일봉) 전환한 종목 목록. 전환 없는 날은 빈 목록. */
export async function holdingFlips(positions) {
  const holdings = (positions || []).filter((item) => item.ticker && item.ticker !== 'CASH' && HOLDING_TICKER.test(item.ticker)).slice(0, 30);
  if (!holdings.length) return { checked: 0, items: [], failed: 0 };
  const tickers = [...new Set(holdings.map((item) => item.ticker))];
  const results = await lookup(tickers);
  const items = [];
  let failed = 0;
  tickers.forEach((ticker, i) => {
    const item = results[i];
    if (!item || item.status !== 'ok') { failed += 1; return; }
    if (item.flip_today) items.push({ ...item, name: holdings.find((row) => row.ticker === ticker)?.name || ticker });
  });
  return { checked: tickers.length, items, failed };
}

export function openSupertrendChart(ticker, name) {
  try {
    const prefs = JSON.parse(localStorage.getItem(CHART_PREF_KEY) || '{}');
    localStorage.setItem(CHART_PREF_KEY, JSON.stringify({ ...prefs, ticker, name: name || ticker, atr: 10, factor: 3 }));
  } catch { /* 저장 불가 브라우저: 차트는 마지막 종목으로 열린다 */ }
  window.navigate('supertrend-chart');
}

export function flipLabelHtml(item) {
  const up = item.flip_today === 'up';
  return `<span class="st-flip-tag ${up ? 'is-st-up' : 'is-st-down'}">${up ? '▲ 상승 전환' : '▼ 하락 전환'}</span>`;
}

/** 브리핑 '추세 전환' 탭 본문. 전환이 없으면 조용한 한 줄만. */
export function holdingFlipsHtml(flips) {
  if (!flips) return '<div class="dash-empty"><p>보유종목 슈퍼트렌드를 확인하는 중…</p></div>';
  if (!flips.checked) return '<div class="dash-empty"><p>슈퍼트렌드를 확인할 보유 종목이 없습니다.</p></div>';
  if (!flips.items.length) {
    return `<div class="dash-empty"><p>오늘 추세가 바뀐 보유종목이 없습니다. (${flips.checked}종목 확인${flips.failed ? ` · ${flips.failed}종목 조회 불가` : ''})</p></div>`;
  }
  return `<ol class="dash-brief-list">${flips.items.map((item, index) => `<li>
      <span class="dash-rank ${item.flip_today === 'down' ? 'is-sell' : ''}">${index + 1}</span>
      <span class="dash-brief-name"><strong>${esc(item.name)}</strong><small>${esc(item.ticker)}</small></span>
      ${flipLabelHtml(item)}
      <span class="dash-brief-price"><button type="button" class="dash-link" data-st-chart="${esc(item.ticker)}" data-st-name="${esc(item.name)}">차트 <i class="fa-solid fa-arrow-right"></i></button></span>
      <ul class="dash-brief-reasons"><li>${esc(item.as_of)} 종가 확정 기준 · 직전 확정 주봉 ${dirText(item.weekly_dir)}${item.distance_pct !== null ? ` · 새 추세선까지 ${Number(item.distance_pct).toFixed(1)}%` : ''}</li></ul>
    </li>`).join('')}</ol>
    <p class="dash-note">일봉 슈퍼트렌드(ATR 10 × 3) 방향이 바뀐 날에만 표시합니다. 참고 정보이며 매매 지시·추천 규칙이 아닙니다.${flips.failed ? ` ${flips.failed}종목은 시세 조회 불가.` : ''}</p>`;
}

/** 내 자산 위 알림 줄. 전환이 없으면 빈 문자열. */
export function holdingFlipsBannerHtml(flips) {
  if (!flips?.items?.length) return '';
  return `<div class="st-flip-banner" role="status"><i class="fa-solid fa-bolt" aria-hidden="true"></i><b>오늘 슈퍼트렌드 전환</b>
    ${flips.items.map((item) => `<button type="button" class="st-flip-item" data-st-chart="${esc(item.ticker)}" data-st-name="${esc(item.name)}">${esc(item.name)} ${flipLabelHtml(item)}</button>`).join('')}
    <small>${esc(flips.items[0].as_of)} 종가 확정 · 참고용</small></div>`;
}

export function bindSupertrendLinks(root) {
  root.querySelectorAll('[data-st-chart]').forEach((button) => button.addEventListener('click', () => openSupertrendChart(button.dataset.stChart, button.dataset.stName)));
}
