// 추천 종목의 판단 근거 표시(2026-10-06, 클로드코드·코덱스 합의안 A): 기준일 갭하락·같은 날 업종 집중·최근 지분공시.
// 추천을 막거나 순서를 바꾸지 않는 참고 정보다. 사용: 마크업에 contextSlot(symbol)을 넣고 그린 뒤 fillPickContext(root).
import { authRequest } from '../auth.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const CACHE_MS = 5 * 60 * 1000;
let cached = null;
let cachedLending = null; // Codex 수정: CODX-SL-001 (2026-10-08), 별도 수급 참고자료 조회 캐시.

function load() {
  if (cached && Date.now() - cached.at < CACHE_MS) return cached.promise;
  const promise = authRequest('/api/market/pick-context').catch(() => null);
  cached = { at: Date.now(), promise };
  promise.then((data) => { if (!data) cached = null; });   // 실패하면 다음에 다시 묻는다
  return promise;
}

function loadLending() {
  if (cachedLending && Date.now() - cachedLending.at < CACHE_MS) return cachedLending.promise;
  const promise = authRequest('/api/market/short-lending-reference').catch(() => null);
  cachedLending = { at: Date.now(), promise };
  promise.then((data) => { if (!data) cachedLending = null; });
  return promise;
}


export function contextSlot(symbol) {
  return `<span class="pick-ctx is-loading" data-ctx-symbol="${esc(symbol)}"></span>`;
}

const shortDate = (iso) => (iso ? `${iso.slice(5, 7)}/${iso.slice(8, 10)}` : '');
// Codex 수정: CODX-SL-001 (2026-10-08), 방향 설명만 표시하고 점수·매매 신호로 쓰지 않는다.
const trend = { increase: '증가', decrease: '감소', flat: '변화 없음', mixed: '혼조' };
const numeric = (value) => value !== null && value !== undefined && Number.isFinite(Number(value)) ? Number(value).toFixed(2) : '-';


/** 한 종목의 판단 근거 칩. 특이사항이 없으면 '특이사항 없음' 한 줄. */
export function contextHtml(item, lending = null) {
  if (!item) return '';
  const chips = [];
  const gap = item.gap || {};
  if (gap.status === 'warning') {
    chips.push(`<span class="pick-ctx-chip is-caution" title="${esc(`${gap.as_of} 시가 ${gap.open?.toLocaleString()} / ${gap.previous_date} 종가 ${gap.previous_close?.toLocaleString()} (이미 관측된 기준일 값)`)}">⚠ 기준일 갭하락 ${esc(gap.gap_pct.toFixed(2))}%</span>`);
  }
  if (item.same_sector?.length) {
    const others = item.same_sector.map((row) => row.name).join(', ');
    chips.push(`<span class="pick-ctx-chip is-info" title="${esc(`같은 날 같은 업종 추천: ${others}`)}">같은 업종 추천 ${item.same_sector.length + 1}종목 · ${esc(item.sector || '')}</span>`);
  }
  if (item.recent_activity) {
    const kinds = Object.entries(item.recent_activity.kinds || {}).map(([label, count]) => `${label} ${count}`).join(' · ');
    chips.push(`<span class="pick-ctx-chip" title="${esc(`최근 7일 지분·자사주 공시: ${kinds}. 좋다·나쁘다를 판정하지 않습니다. 자사주·내부자 공시 화면에서 원문을 확인하세요.`)}">최근 지분·자사주 공시 ${esc(item.recent_activity.count)}건${item.recent_activity.latest_date ? ` (${esc(shortDate(item.recent_activity.latest_date))})` : ''}</span>`);
  }
  if (lending?.status === 'available' || lending?.status === 'partial') {
    const s = lending.short || {}, l = lending.loan || {};
    const shortLabel = s.status === 'ok' ? trend[s.direction_60] || '혼조' : '자료 부족';
    const loanLabel = l.status === 'ok' ? trend[l.direction_60] || '혼조' : '자료 부족';
    const detail = `공매도 거래비중 60일 ${shortLabel} (${(s.blocks_20_pct || []).join(' → ')}%), 최근 20일 변동성 ${numeric(s.volatility_20_pp)}%p, 기준 ${s.as_of || '-'}; 대차잔고 60일 ${loanLabel} (${numeric(l.change_pct_60)}%), 기준 ${l.as_of || '-'}; 수집 ${lending.captured_at || '-'}; ${lending.available_before_open ? '개장 전 수집' : '개장 전 수집 아님'}. 추천 규칙에 반영되지 않습니다.`;
    chips.push(`<span class="pick-ctx-chip is-info" title="${esc(detail)}">공매도 거래 ${esc(shortLabel)} · 대차잔고 ${esc(loanLabel)}</span>`);
  } else if (lending?.status === 'pending') {
    chips.push('<span class="pick-ctx-chip is-muted">공매도·대차 자료 수집 전</span>');
  }
  if (gap.status === 'unavailable' && !chips.length) return '<span class="pick-ctx-chip is-muted">판단 근거: 기준일 시세 확인 불가</span>';
  return chips.length ? chips.join('') : '<span class="pick-ctx-chip is-muted">갭·업종 집중·지분공시 특이사항 없음</span>';
}

export async function fillPickContext(root) {
  const slots = [...root.querySelectorAll('[data-ctx-symbol]')];
  if (!slots.length) return;
  const [data, lending] = await Promise.all([load(), loadLending()]);
  slots.forEach((slot) => {
    if (!slot.isConnected) return;
    const item = data?.available ? data.items?.[slot.dataset.ctxSymbol] : null;
    slot.classList.remove('is-loading');
    slot.innerHTML = data ? contextHtml(item, lending?.items?.[slot.dataset.ctxSymbol]) : '<span class="pick-ctx-chip is-muted">판단 근거 조회 실패</span>';
    if (data?.note) slot.title = data.note;
  });
}
