// 추천 종목 아래 '섹터 · 섹터 순환 구역' 표시(EINT-022). 참고용이며 추천 순서·규칙과 무관하다.
// 사용: 마크업에 sectorSlot(symbol)을 넣고, 그린 뒤 fillSectorBadges(root)를 호출한다.
import { authRequest } from '../auth.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const QUADRANT = {
  leading: ['주도', '#22c55e'], improving: ['개선', '#3b8bff'], weakening: ['약화', '#f5b800'], lagging: ['부진', '#ff4d5e'],
};

// 같은 종목을 여러 영역(브리핑·의사결정 큐 등)이 동시에 그려도 서버에는 한 번만 묻고 5분간 결과를 함께 쓴다.
const CACHE_MS = 5 * 60 * 1000;
const cache = new Map();
function lookup(symbols) {
  const now = Date.now();
  const missing = symbols.filter((symbol) => !(cache.has(symbol) && now - cache.get(symbol).at < CACHE_MS)).slice(0, 30);
  if (missing.length) {
    const batch = authRequest(`/api/market/stock-sectors?symbols=${encodeURIComponent(missing.join(','))}`)
      .then(({ items }) => Object.fromEntries((items || []).map((item) => [item.symbol, item])))
      .catch(() => ({}));
    missing.forEach((symbol) => cache.set(symbol, { at: now, promise: batch.then((bySymbol) => {
      if (!bySymbol[symbol]) cache.delete(symbol);   // 실패한 종목은 다음에 다시 묻는다
      return bySymbol[symbol];
    }) }));
  }
  return Promise.all(symbols.map((symbol) => cache.get(symbol)?.promise))
    .then((items) => Object.fromEntries(symbols.map((symbol, index) => [symbol, items[index]])));
}

export function sectorSlot(symbol) {
  return `<span class="sector-badge is-loading" data-sector-symbol="${esc(symbol)}">섹터 확인 중</span>`;
}

export function sectorBadgeHtml(item) {
  if (!item || !item.sector_name) {
    return `<span class="sector-badge is-none" title="${esc(item?.ksic ? `DART 업종코드 ${item.ksic}` : '')}">섹터 ${esc(item?.note || '정보 없음')}</span>`;
  }
  const [label, color] = QUADRANT[item.quadrant] || [];
  const moved = item.quadrant_4w_ago && item.quadrant && item.quadrant_4w_ago !== item.quadrant
    ? ` <small>(${esc((QUADRANT[item.quadrant_4w_ago] || [])[0])}→${esc(label)})</small>` : '';
  const title = `DART 업종코드 ${item.ksic || '-'} · 섹터 순환 기준 ${item.rotation_as_of || '-'}`;
  return `<span class="sector-badge" title="${esc(title)}"><b>${esc(item.sector_name)}</b>${label
    ? `<i style="--q:${color}">${esc(label)}</i>${moved}` : `<small>${esc(item.note || '')}</small>`}</span>`;
}

export async function fillSectorBadges(root) {
  const slots = [...root.querySelectorAll('[data-sector-symbol]')];
  const symbols = [...new Set(slots.map((slot) => slot.dataset.sectorSymbol).filter(Boolean))];
  if (!symbols.length) return;
  const bySymbol = await lookup(symbols);
  slots.forEach((slot) => { if (slot.isConnected) slot.outerHTML = sectorBadgeHtml(bySymbol[slot.dataset.sectorSymbol]); });
}
