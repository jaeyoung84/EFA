// 추천 종목 아래 '섹터 · 섹터 순환 구역' 표시(EINT-022). 참고용이며 추천 순서·규칙과 무관하다.
// 사용: 마크업에 sectorSlot(symbol)을 넣고, 그린 뒤 fillSectorBadges(root)를 호출한다.
import { authRequest } from '../auth.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const QUADRANT = {
  leading: ['주도', '#22c55e'], improving: ['개선', '#3b8bff'], weakening: ['약화', '#f5b800'], lagging: ['부진', '#ff4d5e'],
};

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
  try {
    const { items } = await authRequest(`/api/market/stock-sectors?symbols=${encodeURIComponent(symbols.join(','))}`);
    const bySymbol = Object.fromEntries((items || []).map((item) => [item.symbol, item]));
    slots.forEach((slot) => { if (slot.isConnected) slot.outerHTML = sectorBadgeHtml(bySymbol[slot.dataset.sectorSymbol]); });
  } catch {
    slots.forEach((slot) => { if (slot.isConnected) slot.outerHTML = sectorBadgeHtml(null); });
  }
}
