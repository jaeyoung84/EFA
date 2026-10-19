// EINT-058 추천 종목 하락 구간의 공시·뉴스 동반 여부 칩. 참고 표시이며 추천을 막거나 순서를 바꾸지 않는다.
// 사용: 마크업에 declineSlot(symbol)을 넣고 그린 뒤 fillPickDecline(root).
import { authRequest } from '../auth.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const CACHE_MS = 5 * 60 * 1000;
let cached = null;
const md = (iso) => (iso ? `${iso.slice(5, 7)}/${iso.slice(8, 10)}` : '');

function load() {
  if (cached && Date.now() - cached.at < CACHE_MS) return cached.promise;
  const promise = authRequest('/api/market/pick-decline-context').catch(() => null);
  cached = { at: Date.now(), promise };
  promise.then((data) => { if (!data) cached = null; });
  return promise;
}

export function declineSlot(symbol) {
  return `<span class="pick-ctx pick-decline is-loading" data-decline-symbol="${esc(symbol)}"></span>`;
}

export function declineHtml(item) {
  if (!item || item.status !== 'ok') {
    return `<span class="pick-ctx-chip is-muted" title="${esc(item?.reason || '')}">하락 구간 공시·뉴스 확인 불가</span>`;
  }
  const range = `${md(item.start)}~${md(item.end)}`;
  const ret = item.return_pct === null || item.return_pct === undefined ? '' : ` ${item.return_pct > 0 ? '+' : ''}${Number(item.return_pct).toFixed(1)}%`;
  const disclosures = item.disclosures;
  const news = item.news;
  const tagged = (disclosures || []).filter((row) => row.tag);
  const parts = [];
  parts.push(disclosures === null ? '공시 확인 불가' : `공시 ${disclosures.length}${tagged.length ? `(${[...new Set(tagged.map((row) => row.tag))].slice(0, 2).join('·')})` : ''}`);
  parts.push(news === null ? '뉴스 확인 불가' : `뉴스 ${news.length}${item.news_partial ? '+(일부만 확인)' : ''}`);
  const none = disclosures !== null && news !== null && !disclosures.length && !news.length && !item.news_partial;
  const tone = tagged.length ? 'is-caution' : none ? 'is-muted' : 'is-info';
  const lines = [
    `하락 구간 ${item.start} ~ ${item.end} (기준일 포함 최근 5거래일) 수익률${ret || ' -'}, 가장 크게 떨어진 날 ${item.worst_day?.date || '-'} ${item.worst_day ? `${item.worst_day.pct}%` : ''}`,
    ...(disclosures || []).slice(0, 6).map((row) => `공시 ${row.date} ${row.title}${row.tag ? ` [${row.tag}]` : ''}`),
    ...(news || []).slice(0, 6).map((row) => `뉴스 ${row.date} ${row.title}${row.source ? ` (${row.source})` : ''}`),
    item.disclosure_error || '', item.news_error || '',
    item.news_partial ? '한투 뉴스는 최신 40건만 조회돼 구간 앞부분 기사는 빠졌을 수 있습니다.' : '',
    '연구에서는 뉴스를 동반한 급락은 이어지고, 동반하지 않은 급락은 되돌아오는 경향이 보고됐습니다(Chan 2003, Savor 2012). 이 시스템에서 검증한 규칙은 아니며 추천에 반영되지 않습니다.',
  ].filter(Boolean).join('\n');
  return `<span class="pick-ctx-chip ${tone}" title="${esc(lines)}">하락 구간 ${esc(range)}${esc(ret)} · ${none ? '공시·뉴스 없음' : esc(parts.join(' · '))}</span>`;
}

export async function fillPickDecline(root) {
  const slots = [...root.querySelectorAll('[data-decline-symbol]')];
  if (!slots.length) return;
  const data = await load();
  slots.forEach((slot) => {
    if (!slot.isConnected) return;
    slot.classList.remove('is-loading');
    slot.innerHTML = data?.available ? declineHtml(data.items?.[slot.dataset.declineSymbol]) : '<span class="pick-ctx-chip is-muted">하락 구간 공시·뉴스 조회 실패</span>';
  });
}
