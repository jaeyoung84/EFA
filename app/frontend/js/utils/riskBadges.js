// 추천 종목의 위험 공시·거래소 지정 상태 표시(EINT-025). 참고용이며 추천 순서·규칙과 무관하다.
// 사용: 마크업에 riskSlot(symbol) 또는 riskSlot(symbol, { full: true })를 넣고, 그린 뒤 fillRiskBadges(root)를 호출한다.
import { authRequest } from '../auth.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const SOURCE = { dart: 'DART 공시', krx: '거래소 지정' };

// full: 신호가 있으면 펼칠 수 있는 목록. hideClean: 신호가 없으면 아무것도 그리지 않는다(요약 배지가 따로 있을 때).
// 같은 종목을 여러 영역(브리핑·의사결정 큐 등)이 동시에 그려도 서버에는 한 번만 묻고 5분간 결과를 함께 쓴다.
const CACHE_MS = 5 * 60 * 1000;
const cache = new Map();
function lookup(symbols) {
  const now = Date.now();
  const missing = symbols.filter((symbol) => !(cache.has(symbol) && now - cache.get(symbol).at < CACHE_MS)).slice(0, 30);
  if (missing.length) {
    const batch = authRequest(`/api/market/stock-risk?symbols=${encodeURIComponent(missing.join(','))}`)
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

export function riskSlot(symbol, { full = false, hideClean = false } = {}) {
  return `<span class="risk-badge is-loading" data-risk-symbol="${esc(symbol)}"${full ? ' data-risk-full' : ''}${hideClean ? ' data-risk-hide-clean' : ''}>위험 공시 확인 중</span>`;
}

function headline(item) {
  const count = item.flags.length;
  if (item.level === 'high') return ['is-high', `⛔ 위험 신호 ${count}건`];
  if (item.level === 'caution') return ['is-caution', `⚠ 주의 신호 ${count}건`];
  const unchecked = ['dart', 'krx'].filter((source) => !item.checked.includes(source)).map((source) => SOURCE[source]);
  return ['is-none', unchecked.length ? `위험 신호 없음 · ${unchecked.join('·')} 미확인` : `위험 신호 없음(${item.window_days}일)`];
}

function flagLine(flag) {
  const when = flag.date ? `<small>${esc(flag.date)}</small> ` : '';
  const title = flag.url
    ? `<a href="${esc(flag.url)}" target="_blank" rel="noopener noreferrer">${esc(flag.title)}</a>`
    : esc(flag.title);
  return `<li class="is-${esc(flag.severity)}">${when}<b>${esc(flag.label)}</b> — ${title}</li>`;
}

export function riskBadgeHtml(item, { full = false, hideClean = false } = {}) {
  if (!item) return '<span class="risk-badge is-none">위험 공시 확인 실패</span>';
  const [cls, text] = headline(item);
  const errors = Object.values(item.errors || {});
  const tip = [...item.flags.map((flag) => `${flag.date ? `${flag.date} ` : ''}${flag.label}: ${flag.title}`), ...errors].join('\n');
  if (hideClean && !item.flags.length) return '';
  if (!full || !item.flags.length) {
    return `<span class="risk-badge ${cls}" title="${esc(tip || `최근 ${item.window_days}일 DART 공시·거래소 지정 상태 기준`)}">${esc(text)}</span>`;
  }
  return `<details class="risk-badge ${cls} is-full"><summary>${esc(text)} <small>최근 ${esc(item.window_days)}일</small></summary>
    <ul>${item.flags.map(flagLine).join('')}</ul>${errors.length ? `<p>${errors.map(esc).join(' · ')}</p>` : ''}
    <p class="risk-note">참고용 — 추천 규칙과 무관하며 원문을 꼭 확인하세요.</p></details>`;
}

export async function fillRiskBadges(root) {
  const slots = [...root.querySelectorAll('[data-risk-symbol]')];
  const symbols = [...new Set(slots.map((slot) => slot.dataset.riskSymbol).filter(Boolean))];
  if (!symbols.length) return;
  const bySymbol = await lookup(symbols);   // 실패한 종목은 '확인 실패'로 표시
  slots.forEach((slot) => {
    if (slot.isConnected) slot.outerHTML = riskBadgeHtml(bySymbol[slot.dataset.riskSymbol], {
      full: slot.hasAttribute('data-risk-full'), hideClean: slot.hasAttribute('data-risk-hide-clean') });
  });
}
