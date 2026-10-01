import { homeView }            from './views/home.js';
import { serverResourcesView } from './views/serverResources.js';
import { worldMarketsView }   from './views/worldMarkets.js';
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
import { api }                 from './api.js';
import { restoreFormState, saveFormState } from './utils/localState.js';
import { initAuth, recordUsage } from './auth.js';

const app        = document.getElementById('app');
const breadcrumb = document.getElementById('breadcrumb');
const TOPBAR_MARKETS = ['^KS11', '^IXIC', 'KRW=X'];
const TOPBAR_REFRESH_MS = 30_000;
const VISITOR_HEARTBEAT_MS = 30_000;
const VISITOR_ID_KEY = 'investment_analysis_visitor_id';

// 투자 판단에 쓰는 화면만 남긴다. 예전 교육·데모 화면 주소는 대시보드로 돌아간다.
const routes = {
  'home':                    { label: '대시보드',                  render: () => homeView(app, navigate) },
  'strategy-board':          { label: '전략 추천',                  render: () => strategyBoardView(app) },
  'my-strategies':           { label: '매매 기록·연구',             render: () => strategyListView(app) },
  'strategy-backtest':       { label: '실제 가격 백테스트',         render: () => strategyBacktestView(app) },
  'leverage-dca':            { label: '레버리지 DCA 자동매매(모의)', render: () => leverageDcaView(app) },
  'portfolio-holdings':      { label: '내 보유자산',                render: () => portfolioHoldingsView(app) },
  'portfolio-risk':          { label: '실제 보유자산 리스크 분석',   render: () => portfolioRiskView(app) },
  'portfolio-combination':   { label: '자산 조합 (상관관계)',        render: () => portfolioCombinationView(app) },
  'world-markets':           { label: '시장 현황',                  render: () => worldMarketsView(app) },
  'asset-classes':           { label: '자산군 차트',                render: () => assetClassesView(app) },
  'today-gainers':           { label: '오늘의 상승 종목',            render: () => todayGainersView(app) },
  'today-sobujang':          { label: '소부장 종목',                render: () => todaySobujangView(app) },
  'macro-realtime':          { label: '거시경제 (실시간)',           render: () => macroRealtimeView(app) },
  'company-financial':       { label: '기업 재무',                  render: () => companyFinancialView(app) },
  'dart-company-search':     { label: 'DART 기업 검색',             render: () => dartCompanySearchView(app) },
  'dart-financial-analysis': { label: 'DART 재무 분석',             render: () => dartFinancialAnalysisView(app) },
  'industry-analysis':       { label: '산업 비교',                  render: () => industryAnalysisView(app) },
  'server-resources':        { label: '시스템 상태',                render: () => serverResourcesView(app) },
};

const NAV_SECTIONS = {
  decisions: ['strategy-board', 'my-strategies', 'strategy-backtest', 'leverage-dca'],
  assets: ['portfolio-holdings', 'portfolio-risk', 'portfolio-combination'],
  market: ['world-markets', 'asset-classes', 'today-gainers', 'today-sobujang', 'macro-realtime',
           'company-financial', 'dart-company-search', 'dart-financial-analysis', 'industry-analysis'],
  settings: ['server-resources'],
};

let currentView = null;
const MOBILE_BREAKPOINT = 1024;

// 대시보드·포트폴리오·전략 연구소 핵심 화면은 퀀트 트레이딩 터미널 다크
// 테마를 쓴다(styles.css의 body.theme-terminal 규칙). 나머지 화면은 아직
// 기존 라이트 테마를 그대로 쓴다.
const TERMINAL_THEME_VIEWS = [
  'home',
  'portfolio-holdings', 'portfolio-risk',
  'strategy-board', 'strategy-backtest', 'my-strategies',
];

function navigate(requested) {
  const view = routes[requested] ? requested : 'home';
  const route = routes[view];
  currentView = view;
  document.body.classList.toggle('theme-terminal', TERMINAL_THEME_VIEWS.includes(view));

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
  const digits = label === 'USD/KRW' ? 2 : 0;
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

async function refreshTopbarMarkets() {
  const stamp = document.getElementById('ticker-stamp');
  const nodes = [...document.querySelectorAll('.ticker-item[data-market-label]')];
  if (!nodes.length) return;

  try {
    const data = await api.marketSnapshot({ tickers: TOPBAR_MARKETS });
    const byLabel = Object.fromEntries((data.items || []).map((item) => [item.label, item]));

    nodes.forEach((node) => {
      const label = node.dataset.marketLabel;
      const valueEl = node.querySelector('b');
      const changeEl = node.querySelector('em');
      const item = byLabel[label];

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

    if (stamp) stamp.textContent = `조회 시각 ${formatTimestamp(data.fetched_at)} (15분 지연)`;
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
initAuth().then(() => { if (currentView) recordUsage(currentView); });
setInterval(checkHealth, 30000);
refreshTopbarMarkets();
setInterval(refreshTopbarMarkets, TOPBAR_REFRESH_MS);
refreshVisitorCount();
setInterval(refreshVisitorCount, VISITOR_HEARTBEAT_MS);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') refreshVisitorCount();
});

// Boot
// 공유된 index.html?view=xxx 주소는 그 화면으로 바로 진입한다. 없는 화면은 대시보드로 간다.
navigate(new URLSearchParams(window.location.search).get('view') || 'home');
