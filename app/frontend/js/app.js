import { dashboardView }       from './views/dashboard.js';
import { watchlistView }       from './views/watchlist.js';
import { researchNotesView }   from './views/researchNotes.js';
import { initShell }           from './shell.js';
import { serverResourcesView } from './views/serverResources.js';
import { worldMarketsView }   from './views/worldMarkets.js';
import { sectorRotationView } from './views/sectorRotation.js';
import { externalPicksView } from './views/externalPicks.js';
import { assetClassesView }   from './views/assetClasses.js';
import { todayGainersView }   from './views/todayGainers.js';
import { todaySobujangView }  from './views/todaySobujang.js';
import { portfolioHoldingsView } from './views/portfolioHoldings.js';
import { portfolioRiskView } from './views/portfolioRisk.js';
import { strategyBacktestView } from './views/strategyBacktest.js';
import { strategyListView } from './views/strategyList.js';
import { strategyBoardView } from './views/strategyBoard.js';
import { leverageDcaView } from './views/leverageDca.js';
import { portfolioCombinationView } from './views/portfolioCombination.js';
import { macroRealtimeView }    from './views/macroRealtime.js';
import { industryAnalysisView } from './views/industryAnalysis.js';
import { dartCompanySearchView } from './views/dartCompanySearch.js';
import { companyFinancialView } from './views/companyFinancial.js';
import { dartFinancialAnalysisView } from './views/dartFinancialAnalysis.js';
import { dartMarketActivityView } from './views/dartMarketActivity.js';
import { api }                 from './api.js';
import { restoreFormState, saveFormState } from './utils/localState.js';
import { initAuth, recordUsage } from './auth.js';

const app        = document.getElementById('app');
// EFA 디자인: 모든 ApexCharts 차트의 기본값을 다크 테마로 둔다(화면별 설정이 있으면 그 값이 우선).
if (typeof window.Apex === 'undefined') window.Apex = {};
Object.assign(window.Apex, { theme: { mode: 'dark' }, chart: { background: 'transparent', foreColor: '#8a97ad' },
  grid: { borderColor: '#1e2b42' }, tooltip: { theme: 'dark' } });
const breadcrumb = document.getElementById('breadcrumb');
// 시세 띠: 화면 표시 이름 → 시세 티커. /api/market/snapshot은 지연 시세다.
const TOPBAR_MARKETS = { 'KOSPI': '^KS11', 'KOSDAQ': '^KQ11', 'NASDAQ': '^IXIC', 'S&P500': '^GSPC',
  'S&P500 선물': 'ES=F', '나스닥100 선물': 'NQ=F', 'USD/KRW': 'KRW=X' };
const TOPBAR_REFRESH_MS = 30_000;
const VISITOR_HEARTBEAT_MS = 30_000;
const VISITOR_ID_KEY = 'investment_analysis_visitor_id';

// 투자 판단에 쓰는 화면만 남긴다. 예전 교육·데모 화면 주소는 대시보드로 돌아간다.
const routes = {
  'home':                    { label: '대시보드',                  render: () => dashboardView(app) },
  'watchlist':               { label: '관심종목',                  render: () => watchlistView(app) },
  'research-notes':          { label: '리서치 노트',               render: () => researchNotesView(app) },
  'strategy-board':          { label: '전략 추천',                  render: () => strategyBoardView(app) },
  'my-strategies':           { label: '매매 기록·연구',             render: () => strategyListView(app) },
  'strategy-backtest':       { label: '실제 가격 백테스트',         render: () => strategyBacktestView(app) },
  'leverage-dca':            { label: '레버리지 DCA 자동매매(모의)', render: () => leverageDcaView(app) },
  'external-picks':          { label: '외부 추천 기록',             render: () => externalPicksView(app) },
  'portfolio-holdings':      { label: '내 보유자산',                render: () => portfolioHoldingsView(app) },
  'portfolio-risk':          { label: '실제 보유자산 리스크 분석',   render: () => portfolioRiskView(app) },
  'portfolio-combination':   { label: '자산 조합 (상관관계)',        render: () => portfolioCombinationView(app) },
  'world-markets':           { label: '시장 현황',                  render: () => worldMarketsView(app) },
  'sector-rotation':         { label: '섹터 순환 (RRG)',            render: () => sectorRotationView(app) },
  'asset-classes':           { label: '자산군 차트',                render: () => assetClassesView(app) },
  'today-gainers':           { label: '오늘의 상승 종목',            render: () => todayGainersView(app) },
  'today-sobujang':          { label: '소부장 종목',                render: () => todaySobujangView(app) },
  'macro-realtime':          { label: '거시경제 (일별)',           render: () => macroRealtimeView(app) },
  'company-financial':       { label: '기업 재무',                  render: () => companyFinancialView(app) },
  'dart-company-search':     { label: 'DART 기업 검색',             render: () => dartCompanySearchView(app) },
  'dart-financial-analysis': { label: 'DART 재무 분석',             render: () => dartFinancialAnalysisView(app) },
  'industry-analysis':       { label: '산업 비교',                  render: () => industryAnalysisView(app) },
  'dart-market-activity':  { label: '자사주·내부자 공시',          render: () => dartMarketActivityView(app) },
  'server-resources':        { label: '시스템 상태',                render: () => serverResourcesView(app) },
};

const NAV_SECTIONS = {
  analysis: ['company-financial', 'dart-company-search', 'dart-financial-analysis', 'dart-market-activity', 'industry-analysis'],
  portfolio: ['portfolio-holdings', 'portfolio-risk', 'portfolio-combination'],
  kqs: ['strategy-board', 'my-strategies', 'strategy-backtest', 'leverage-dca', 'external-picks'],
  market: ['world-markets', 'sector-rotation', 'asset-classes', 'today-gainers', 'today-sobujang', 'macro-realtime'],
  watchlist: ['watchlist'],
  notes: ['research-notes'],
  settings: ['server-resources'],
};

let currentView = null;
const MOBILE_BREAKPOINT = 1024;


function navigate(requested) {
  const view = routes[requested] ? requested : 'home';
  const route = routes[view];
  currentView = view;
  // EFA 디자인: 모든 화면이 같은 다크 터미널 테마를 쓴다(styles.css의 body.theme-terminal 규칙).
  document.body.classList.add('theme-terminal');

  // Views that create resources needing teardown (e.g. ApexCharts instances,
  // which keep an internal window-resize listener alive) register a cleanup
  // via window._viewCleanup. Run it before swapping in the next view's HTML,
  // otherwise the orphaned chart's resize handler fires against a detached
  // container and throws (NaN width/transform errors from apexcharts).
  if (typeof window._viewCleanup === 'function') {
    try { window._viewCleanup(); } catch (e) { /* noop */ }
  }
  window._viewCleanup = null;

  // Update active sidebar link
  document.querySelectorAll('.nav-item[data-view], .sidebar-link[data-view]').forEach(a => {
    a.classList.toggle('active', a.dataset.view === view);
  });

  // Update breadcrumb
  if (breadcrumb) breadcrumb.textContent = route.label;

  // 현재 화면이 속한 사이드바 섹션만 펼치고 나머지는 닫는다 (사용 중인 메뉴만 열림)
  const activeSections = Object.keys(NAV_SECTIONS).filter((section) => NAV_SECTIONS[section].includes(view));
  if (typeof window._setActiveNavSections === 'function') window._setActiveNavSections(activeSections);

  route.render();
  recordUsage(view);
  // MongoDB를 사용하지 않는 화면의 사용자 입력은 화면별로 브라우저에 보관한다.
  // 렌더링 직후 실행해 각 뷰의 기본값 대신 마지막 입력값을 복원한다.
  requestAnimationFrame(() => restoreFormState(view, app));

  if (window.innerWidth <= MOBILE_BREAKPOINT && typeof closeMainnav === 'function') closeMainnav();
}

window.navigate = navigate;

// 인라인 onclick 대신 data-nav·data-action으로 연결한다(CSP가 인라인 스크립트를 막기 때문).
document.addEventListener('click', (event) => {
  const nav = event.target.closest('[data-nav]');
  if (nav) { event.preventDefault(); navigate(nav.dataset.nav); return; }
  const action = event.target.closest('[data-action]')?.dataset.action;
  if (action === 'toggle-mainnav') window.toggleMainnav?.();
  if (action === 'close-mainnav') window.closeMainnav?.();
  if (action === 'dismiss-service-notice') {
    try { localStorage.setItem(SERVICE_NOTICE_KEY, SERVICE_NOTICE_VERSION); } catch { /* 저장 불가 브라우저: 이번 화면에서만 닫힘 */ }
    document.querySelector('[data-service-notice]')?.setAttribute('hidden', '');
  }
});

// 서비스 성격 안내는 처음 방문한 브라우저에서만 보인다. 안내 문구가 바뀌면 버전을 올려 다시 한 번 보여 준다.
const SERVICE_NOTICE_KEY = 'efa_service_notice_ack';
const SERVICE_NOTICE_VERSION = '2026-10-03';
try {
  if (localStorage.getItem(SERVICE_NOTICE_KEY) !== SERVICE_NOTICE_VERSION) document.querySelector('[data-service-notice]')?.removeAttribute('hidden');
} catch { document.querySelector('[data-service-notice]')?.removeAttribute('hidden'); }

// 개별 뷰마다 저장 코드를 반복하지 않고 모든 입력·선택 변경을 자동 보관한다.
app.addEventListener('input', (event) => saveFormState(currentView, app));
app.addEventListener('change', (event) => saveFormState(currentView, app));

// Wire up sidebar links
document.querySelectorAll('.nav-item[data-view], .sidebar-link[data-view]').forEach(a => {
  a.addEventListener('click', (e) => {
    e.preventDefault();
    navigate(a.dataset.view);
  });
});

// 데스크톱에서 메뉴 그룹 이름을 누르면 그 그룹의 첫 화면으로 간다(하위 화면은 마우스를 올리면 펼쳐진다).
document.querySelectorAll('.mainnav-trigger[data-first-view]').forEach((trigger) => {
  trigger.addEventListener('click', (event) => {
    if (window.innerWidth <= MOBILE_BREAKPOINT) return;
    event.preventDefault();
    navigate(trigger.dataset.firstView);
  });
});

document.querySelectorAll('[data-view="home"].brand').forEach((button) => {
  button.addEventListener('click', () => navigate('home'));
});

// Health check
async function checkHealth() {
  const dot  = document.getElementById('health-dot');
  const text = document.getElementById('health-text');
  try {
    await api.health();
    if (dot)  dot.style.background  = '#22c55e';
    if (text) text.textContent = '백엔드 연결됨';
  } catch {
    if (dot)  dot.style.background  = '#ef4444';
    if (text) text.textContent = '백엔드 오프라인';
  }
}

function formatMarketValue(label, value) {
  const digits = label === 'USD/KRW' || label.includes('선물') ? 2 : 0;
  return Number(value).toLocaleString('ko-KR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatTimestamp(ts) {
  if (!ts) return '--';
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return '--';
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}

// 코스피200 야간선물(한투, 실시간·30초 캐시): 야간 18:00~05:00에는 진행 중 시세, 그 외에는 마지막 야간 종가.
async function refreshKospiNightFutures() {
  const node = document.querySelector('.ticker-item[data-krx-night]');
  if (!node) return;
  const valueEl = node.querySelector('b');
  const changeEl = node.querySelector('em');
  const tagEl = node.querySelector('small');
  try {
    const data = await fetch('/api/market/krx-futures').then((response) => response.json());
    const night = data.night || {};
    if (data.status !== 'ok' || night.status !== 'ok') throw new Error(night.reason || data.reason || '조회 실패');
    valueEl.textContent = night.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    changeEl.textContent = `${night.change_pct >= 0 ? '+' : ''}${night.change_pct.toFixed(2)}%`;
    changeEl.className = night.change_pct >= 0 ? 'up' : 'dn';
    tagEl.textContent = data.night_session_open ? '야간 진행 중' : '야간 종가';
    node.title = `${night.contract || ''} · 한투 시세(실시간) · 야간 18:00~05:00 KST · 조회 ${formatTimestamp(data.fetched_at)}`;
  } catch (error) {
    valueEl.textContent = '-';
    changeEl.textContent = '조회 실패';
    changeEl.className = '';
    tagEl.textContent = '';
    node.title = String(error.message || error);
  }
}

async function refreshTopbarMarkets() {
  const stamp = document.getElementById('ticker-stamp');
  const nodes = [...document.querySelectorAll('.ticker-item[data-market-label]')];
  if (!nodes.length) return;

  try {
    const data = await api.marketSnapshot({ tickers: Object.values(TOPBAR_MARKETS) });
    const byTicker = Object.fromEntries((data.items || []).map((item) => [item.ticker, item]));

    nodes.forEach((node) => {
      const label = node.dataset.marketLabel;
      const valueEl = node.querySelector('b');
      const changeEl = node.querySelector('em');
      const item = byTicker[TOPBAR_MARKETS[label]];

      if (!item || item.status !== 'ok') {
        valueEl.textContent = '-';
        changeEl.textContent = '조회 실패';
        changeEl.className = '';
        return;
      }

      valueEl.textContent = formatMarketValue(label, item.value);
      const sign = item.change_pct >= 0 ? '+' : '';
      changeEl.textContent = `${sign}${item.change_pct.toFixed(2)}%`;
      changeEl.className = item.change_pct >= 0 ? 'up' : 'dn';
      node.title = `데이터 시각 ${formatTimestamp(item.latest_data_at)}`;
    });

    if (stamp) stamp.textContent = formatTimestamp(data.fetched_at);
    refreshKospiNightFutures();
  } catch {
    nodes.forEach((node) => {
      const valueEl = node.querySelector('b');
      const changeEl = node.querySelector('em');
      valueEl.textContent = '-';
      changeEl.textContent = '조회 실패';
      changeEl.className = '';
    });
    if (stamp) stamp.textContent = '조회 시각 확인 불가';
  }
}

function visitorId() {
  const createId = () => {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID().replaceAll('-', '');
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;
  };
  try {
    const saved = localStorage.getItem(VISITOR_ID_KEY);
    if (saved) return saved;
    const id = createId();
    localStorage.setItem(VISITOR_ID_KEY, id);
    return id;
  } catch {
    return createId();
  }
}

async function refreshVisitorCount() {
  const badge = document.getElementById('visitor-count');
  const text = badge?.querySelector('span');
  if (!text) return;
  try {
    const data = await api.visitorHeartbeat({ visitor_id: visitorId() });
    text.textContent = `현재 접속 ${Number(data.active_visitors || 0).toLocaleString('ko-KR')}명`;
  } catch {
    text.textContent = '현재 접속 확인 불가';
  }
}

checkHealth();
initAuth().then(() => { if (currentView) recordUsage(currentView); initShell(); });
setInterval(checkHealth, 30000);
refreshTopbarMarkets();
setInterval(refreshTopbarMarkets, TOPBAR_REFRESH_MS);
setupMobileTicker();

// 모바일 시세 띠: 높이는 그대로 두고 지수들이 천천히 좌우로 흐른다(손가락을 대면 멈춤). 띠를 탭하면 전체 지수를
// 2열 표로 펼치고 다시 탭하면 접는다. PC는 기존 한 줄 띠 그대로. 움직임 줄이기 설정이면 흐르지 않고 손으로 민다.
function setupMobileTicker() {
  const ticker = document.querySelector('.efa-ticker');
  const track = document.getElementById('ticker-track');
  const toggle = document.getElementById('ticker-toggle');
  if (!ticker || !track || !toggle) return;
  const mobile = window.matchMedia('(max-width: 760px)');
  let resumeTimer = null;
  const measure = () => {
    const shift = Math.max(0, track.scrollWidth - (ticker.clientWidth - 48));
    ticker.style.setProperty('--ticker-shift', `${-shift}px`);
    ticker.style.setProperty('--ticker-duration', `${Math.max(12, shift / 28)}s`);   // 초당 약 28px
  };
  const setOpen = (open) => {
    ticker.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? '시세 접기' : '시세 전체 보기');
    if (!open) requestAnimationFrame(measure);
  };
  ticker.addEventListener('click', (event) => {
    if (!mobile.matches || event.target.closest('a')) return;
    setOpen(!ticker.classList.contains('is-open'));
  });
  ticker.addEventListener('touchstart', () => {
    clearTimeout(resumeTimer);
    ticker.classList.add('is-paused');
  }, { passive: true });
  ticker.addEventListener('touchend', () => {
    resumeTimer = setTimeout(() => ticker.classList.remove('is-paused'), 2500);
  }, { passive: true });
  document.addEventListener('click', (event) => {
    if (ticker.classList.contains('is-open') && !ticker.contains(event.target)) setOpen(false);
  });
  window.addEventListener('resize', measure);
  mobile.addEventListener?.('change', () => { if (!mobile.matches) setOpen(false); measure(); });
  new MutationObserver(() => requestAnimationFrame(measure)).observe(track, { subtree: true, characterData: true, childList: true });
  measure();
}
refreshVisitorCount();
setInterval(refreshVisitorCount, VISITOR_HEARTBEAT_MS);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') refreshVisitorCount();
});

// Boot
// 공유된 index.html?view=xxx 주소는 그 화면으로 바로 진입한다. 없는 화면은 대시보드로 간다.
navigate(new URLSearchParams(window.location.search).get('view') || 'home');
