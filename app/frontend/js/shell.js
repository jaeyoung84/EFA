// EFA 상단바 도구: 종목 검색과 알림(엔진 상태). 화면 공통이며 주문·자동 실행은 없다.
import { authRequest, isAuthenticated, openAuthModal } from './auth.js';
import { attachStockSearch } from './utils/stockSearch.js';

const BELL_REFRESH_MS = 5 * 60 * 1000;
export const PENDING_STOCK_KEY = 'efa_pending_stock';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const kst = (value) => {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime())
    ? date.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : '-';
};

/** 검색에서 고른 종목을 기업 재무 화면이 이어받는다(한 번만 읽고 지운다). */
export function takePendingStock() {
  try {
    const raw = sessionStorage.getItem(PENDING_STOCK_KEY);
    sessionStorage.removeItem(PENDING_STOCK_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function initSearch() {
  const input = document.getElementById('efa-global-search');
  if (!input || input.dataset.ready) return;
  input.dataset.ready = '1';
  attachStockSearch(input, {
    onSelect: (item) => {
      try { sessionStorage.setItem(PENDING_STOCK_KEY, JSON.stringify(item)); } catch { /* 저장 불가 브라우저 */ }
      input.value = '';
      input.blur();
      window.navigate('company-financial');
    },
  });
}

export function bellPanelHtml(health, loggedIn) {
  if (!loggedIn) {
    return '<p class="efa-bell-empty">로그인하면 엔진 실행·추천 게시 상태 알림을 볼 수 있습니다.</p><button type="button" class="efa-bell-action" data-bell-login>로그인</button>';
  }
  if (!health) return '<p class="efa-bell-empty">알림을 불러오지 못했습니다.</p>';
  const alerts = health.alerts || [];
  if (!alerts.length) {
    return `<p class="efa-bell-ok"><i class="fa-solid fa-circle-check"></i> 알림 없음 · 엔진 정상</p>
      <p class="efa-bell-empty">추천 기준일 ${esc(health.published_session || '-')} · 최근 실행 ${esc(kst(health.last_run_finished_at))}</p>`;
  }
  return `<ul class="efa-bell-list">${alerts.map((alert) => `<li class="is-${esc(alert.level)}"><b>${esc(alert.title)}</b><span>${esc(alert.message)}</span></li>`).join('')}</ul>
    <button type="button" class="efa-bell-action" data-bell-board>전략 추천 화면에서 보기</button>`;
}

function initBell() {
  const button = document.getElementById('efa-bell');
  const panel = document.getElementById('efa-bell-panel');
  const count = document.getElementById('efa-bell-count');
  if (!button || !panel || button.dataset.ready) return;
  button.dataset.ready = '1';
  let health = null;

  const render = () => {
    const loggedIn = isAuthenticated();
    const alerts = loggedIn && health ? health.alerts || [] : [];
    count.hidden = !alerts.length;
    count.textContent = String(alerts.length);
    count.classList.toggle('is-error', alerts.some((alert) => alert.level === 'error'));
    panel.innerHTML = bellPanelHtml(health, loggedIn);
    panel.querySelector('[data-bell-login]')?.addEventListener('click', () => { close(); openAuthModal(); });
    panel.querySelector('[data-bell-board]')?.addEventListener('click', () => { close(); window.navigate('strategy-board'); });
  };
  const load = async () => {
    if (!isAuthenticated()) { health = null; render(); return; }
    try { health = await authRequest('/api/kqs/pipeline-health'); } catch { health = null; }
    render();
  };
  const close = () => { panel.hidden = true; button.setAttribute('aria-expanded', 'false'); };
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    const opening = panel.hidden;
    panel.hidden = !opening;
    button.setAttribute('aria-expanded', String(opening));
    if (opening) load();
  });
  document.addEventListener('click', (event) => { if (!panel.hidden && !panel.contains(event.target)) close(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
  load();
  setInterval(load, BELL_REFRESH_MS);
}

export function initShell() {
  initSearch();
  initBell();
}
