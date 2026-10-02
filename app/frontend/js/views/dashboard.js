// EFA 대시보드: 인사·오늘의 투자 브리핑(내 보유종목 뉴스 등)·내 자산 지도·KQS 의사결정 큐·시장 흐름·지수 차트·데이터 상태.
// 모든 숫자는 실제 API 값만 쓴다. 데이터가 없으면 "없음"과 이유를 보여 주고 지어내지 않는다.
// 주문·자동 체결은 없으며, 의사결정 큐의 버튼은 사용자의 수동 판단 기록(POST /api/kqs/decisions)이다.
import { authRequest, currentUserInfo, isAuthenticated, openAuthModal } from '../auth.js';
import { portfolioInsights, portfolioValuation, weightBasis } from './portfolioHoldings.js';
import { DECISION_LABEL, bindDecisionForm, decisionForm } from './strategyBoard.js';
import { fillSectorBadges, sectorSlot } from '../utils/sectorBadges.js';
import { liveSlot, startLiveQuotes } from '../utils/liveQuotes.js';
import { fillRiskBadges, riskSlot } from '../utils/riskBadges.js';
import { exitPlanHtml } from '../utils/exitPlan.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const num = (value) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
const fmt = (value, digits = 0) => (num(value)
  ? Number(value).toLocaleString('ko-KR', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '-');
const tone = (value) => (!num(value) || Number(value) === 0 ? 'is-flat' : Number(value) > 0 ? 'is-up' : 'is-down');
const signed = (value, digits = 2, suffix = '%') => (num(value)
  ? `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(digits)}${suffix}` : '-');
const kstTime = (value, withDate = false) => {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hour12: false,
    ...(withDate ? { month: 'numeric', day: 'numeric' } : {}) });
};

export const ITEM_SHORT = { swing: 'Bollinger-RSI', long_term: 'RSI-OBV', pullback_v4: 'Pullback v4' };
const MARKETS = [
  { id: 'kospi', name: 'KOSPI', ticker: '^KS11', digits: 2 },
  { id: 'kosdaq', name: 'KOSDAQ', ticker: '^KQ11', digits: 2 },
  { id: 'nasdaq', name: 'NASDAQ', ticker: '^IXIC', digits: 2 },
  { id: 'sp500', name: 'S&P500', ticker: '^GSPC', digits: 2 },
];
const FLOW_PERIODS = [['1d', '1일'], ['1w', '1주'], ['1mo', '1개월'], ['3mo', '3개월'], ['1y', '1년']];
const CHART_FRAMES = [['day', '일봉'], ['week', '주봉'], ['month', '월봉']];

// ── 순수 렌더 함수(테스트 대상) ─────────────────────────────

export function greetingHtml(now = new Date(), name = '') {
  const hour = Number(now.toLocaleString('en-US', { timeZone: 'Asia/Seoul', hour: 'numeric', hour12: false })) % 24;
  const [greet, icon] = hour < 5 ? ['편안한 밤입니다', 'fa-moon'] : hour < 12 ? ['좋은 아침입니다', 'fa-sun']
    : hour < 18 ? ['좋은 오후입니다', 'fa-cloud-sun'] : ['좋은 저녁입니다', 'fa-moon'];
  const date = now.toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' });
  return `<i class="fa-solid ${icon} dash-greet-icon" aria-hidden="true"></i>
    <div><h1>${esc(greet)}, ${esc(name || '투자자')}님.</h1>
      <p>${esc(date)}<span>오늘의 추천과 내 기록을 한 화면에서 확인하세요.</span></p></div>`;
}

function decisionBadge(decisions) {
  const last = (decisions || [])[decisions?.length - 1];
  if (!last) return '<span class="dash-badge is-wait">판단 대기</span>';
  const [label] = DECISION_LABEL[last.decision] || [last.decision];
  return `<span class="dash-badge is-${esc(last.decision)}">${esc(label)}</span>`;
}

export function picksOf(board) {
  return (board?.items || []).flatMap((item) => item.picks.map((pick) => ({ ...pick, item: item.item })));
}

// 내 보유종목 주요 뉴스: 비중 높은 종목부터(서버가 정렬), 헤드라인은 원문을 새 창으로 연다.
const safeUrl = (url) => (/^https?:\/\//i.test(String(url || '')) ? String(url) : null);

export function newsAge(iso, now = Date.now()) {
  const at = new Date(iso || '').getTime();
  if (!Number.isFinite(at)) return '';
  const minutes = Math.max(0, Math.round((now - at) / 60000));
  if (minutes < 60) return `${minutes}분 전`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)}시간 전`;
  return new Date(at).toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric' });
}

export function newsHtml(news, now = Date.now()) {
  if (!news) return '<div class="dash-empty"><p>보유종목 뉴스를 불러오는 중…</p></div>';
  if (news.error) return `<div class="dash-empty"><p>뉴스를 불러오지 못했습니다. ${esc(news.error)}</p></div>`;
  if (!news.holdings?.length) {
    return `<div class="dash-empty"><p>${esc(news.note || '내 자산에 국내 보유 종목이 없습니다.')}</p>
      <button type="button" class="dash-btn is-primary" data-dash-go="portfolio-holdings">보유자산 등록</button></div>`;
  }
  if (!news.items?.length) {
    return `<div class="dash-empty"><p>${news.pending?.length ? '보유 종목 뉴스를 모으는 중입니다. 잠시 후(최대 10분) 다시 표시됩니다.' : '최근 보유 종목 관련 뉴스가 없습니다.'}</p></div>`;
  }
  const rows = news.items.map((item) => {
    const url = safeUrl(item.url);
    const title = url
      ? `<a class="dash-news-title" href="${esc(url)}" target="_blank" rel="noopener noreferrer"${item.link_kind === 'original' ? '' : ' title="원문을 찾지 못해 네이버 증권 종목 뉴스로 연결합니다"'}>${esc(item.title)}</a>`
      : `<span class="dash-news-title">${esc(item.title)}</span>`;
    const when = item.published_at ? `<time datetime="${esc(item.published_at)}" title="${esc(kstTime(item.published_at, true))}">${esc(newsAge(item.published_at, now))}</time>` : '';
    return `<li><span class="dash-news-tag" title="보유 비중 ${esc(item.weight_pct)}%">${esc(item.name)}</span>
      <div class="dash-news-body">${title}<span class="dash-news-meta">${esc(item.source || '언론사 미상')}${when ? ' · ' : ''}${when}</span></div></li>`;
  }).join('');
  const fetched = news.fetched_at ? `수집 ${esc(kstTime(news.fetched_at))}` : '';
  return `<ol class="dash-news-list">${rows}</ol>
    <p class="dash-note">보유 비중이 높은 종목 순 · 종목당 최근 3건 · ${fetched}. 한국투자증권 뉴스 제목, 원문은 네이버 뉴스 검색으로 연결합니다.</p>`;
}

export function briefingHtml(tab, { board, outcomes, news, loggedIn }) {
  if (!loggedIn) {
    return `<div class="dash-empty"><p>로그인하면 오늘의 KQS 추천과 내 판단 기록을 볼 수 있습니다.</p>
      <button type="button" class="dash-btn is-primary" data-dash-login>로그인</button></div>`;
  }
  if (tab === 'news') return newsHtml(news);
  if (!board) return '<div class="dash-empty"><p>추천을 불러오는 중…</p></div>';
  if (tab === 'sells') {
    const sells = (board.sell_signals || []).filter((row) => row.state === 'exit_signal');
    if (!(board.sell_signals || []).length) return '<div class="dash-empty"><p>기록된 보유 종목이 없습니다. 매수하면 추천 카드에서 기록하세요.</p></div>';
    if (!sells.length) return `<div class="dash-empty"><p>보유 ${board.sell_signals.length}종목 모두 매도 신호가 없습니다.</p></div>`;
    return `<ol class="dash-brief-list">${sells.map((row, index) => `<li>
      <span class="dash-rank is-sell">${index + 1}</span>
      <span class="dash-brief-name"><strong>${esc(row.name || row.symbol)}</strong><small>${esc(row.symbol)} · ${esc(ITEM_SHORT[row.item] || '-')}</small></span>
      <span class="dash-badge is-sell">매도 신호</span>
      <span class="dash-brief-price">${fmt(row.close)}<small>기준 종가</small></span>
      <ul class="dash-brief-reasons"><li>${esc(row.reason_label || row.detail || '')}</li><li>매수 ${esc(row.bought_on)} · ${fmt(row.fill_price)}원 · ${esc(row.remaining_quantity)}주</li></ul>
    </li>`).join('')}</ol>`;
  }
  if (tab === 'outcomes') {
    if (!outcomes) return '<div class="dash-empty"><p>추천 성과를 불러오는 중…</p></div>';
    if (!outcomes.available) return `<div class="dash-empty"><p>${esc(outcomes.detail || '추천 성과가 아직 없습니다.')}</p></div>`;
    return `<table class="dash-table"><thead><tr><th>아이템</th><th>추천</th><th>5일 관측</th><th>5일 평균</th><th>상승 비율</th><th>상태</th></tr></thead><tbody>
      ${outcomes.items.map((item) => { const five = item.horizons?.['5'] || {}; return `<tr>
        <td>${esc(item.label)}</td><td>${esc(item.pick_count)}건</td><td>${esc(five.observed_count ?? 0)}건</td>
        <td class="${tone(five.mean_return_pct)}">${signed(five.mean_return_pct)}</td>
        <td>${num(five.win_rate_pct) ? `${Number(five.win_rate_pct).toFixed(0)}%` : '-'}</td>
        <td>${five.sample_gate === 'ready' ? '<span class="dash-badge is-buy">판단 가능</span>' : `<span class="dash-badge is-wait">표본 부족 ${esc(five.observed_count ?? 0)}/${esc(outcomes.min_sample)}</span>`}</td></tr>`; }).join('')}
      </tbody></table><p class="dash-note">추천 다음 거래일 시가 기준 사후 관측치(시세 기준일 ${esc(outcomes.through)}). 비용·청산 규칙 미반영.</p>`;
  }
  const health = board.health;
  if (!health) return '<div class="dash-empty"><p>엔진 상태를 확인할 수 없습니다.</p></div>';
  return `<ul class="dash-health">${(health.alerts || []).length
    ? health.alerts.map((alert) => `<li class="is-${esc(alert.level)}"><b>${esc(alert.title)}</b><span>${esc(alert.message)}</span></li>`).join('')
    : `<li class="is-ok"><b>엔진 정상</b><span>추천 기준일 ${esc(health.published_session || '-')} · 최근 실행 ${esc(kstTime(health.last_run_finished_at, true))}</span></li>`}</ul>`;
}

/** 면적 = 비중인 단순 트리맵(합이 비슷하도록 둘로 나누며 긴 변을 자른다). 결과는 % 좌표. */
export function treemapLayout(items, x = 0, y = 0, w = 100, h = 100) {
  const list = items.filter((item) => item.value > 0);
  if (!list.length) return [];
  if (list.length === 1) return [{ ...list[0], x, y, w, h }];
  const total = list.reduce((sum, item) => sum + item.value, 0);
  let acc = 0;
  let cut = 1;
  for (let i = 0; i < list.length - 1; i += 1) {
    acc += list[i].value;
    cut = i + 1;
    if (acc >= total / 2) break;
  }
  const first = list.slice(0, cut);
  const ratio = first.reduce((sum, item) => sum + item.value, 0) / total;
  if (w >= h * 0.9) {
    return [...treemapLayout(first, x, y, w * ratio, h), ...treemapLayout(list.slice(cut), x + w * ratio, y, w * (1 - ratio), h)];
  }
  return [...treemapLayout(first, x, y, w, h * ratio), ...treemapLayout(list.slice(cut), x, y + h * ratio, w, h * (1 - ratio))];
}

export function assetsHtml(state) {
  if (!state.loggedIn) {
    return '<div class="dash-empty"><p>로그인하면 내 보유자산 지도를 볼 수 있습니다.</p><button type="button" class="dash-btn is-primary" data-dash-login>로그인</button></div>';
  }
  if (!state.insight) return `<div class="dash-empty"><p>${esc(state.error || '보유자산을 불러오는 중…')}</p></div>`;
  const positions = state.insight.positions.filter((item) => item.value_krw > 0).slice(0, 12);
  if (!positions.length) {
    return '<div class="dash-empty"><p>등록된 보유자산이 없습니다.</p><button type="button" class="dash-btn is-primary" data-dash-go="portfolio-holdings">보유자산 등록</button></div>';
  }
  const tiles = treemapLayout(positions.map((item) => ({ ...item, value: item.value_krw })));
  return `<div class="dash-treemap" role="img" aria-label="보유자산 비중 지도">${tiles.map((tile) => {
    const cash = tile.ticker === 'CASH';
    const cls = cash ? 'is-cash' : tile.change_pct > 0 ? 'is-up' : tile.change_pct < 0 ? 'is-down' : 'is-flat';
    const size = tile.h < 14 || tile.w < 14 ? ' is-tiny' : tile.h < 26 || tile.w < 24 ? ' is-small' : '';
    return `<div class="dash-tile ${cls}${size}" style="left:${tile.x}%;top:${tile.y}%;width:${tile.w}%;height:${tile.h}%"
      title="${esc(tile.name)} 비중 ${tile.weight_pct.toFixed(1)}%">
      <strong>${esc(tile.name)}</strong><span>${tile.weight_pct.toFixed(1)}%</span>${cash ? '' : `<em>${signed(tile.change_pct)}</em>`}</div>`;
  }).join('')}</div>
  <div class="dash-legend"><span><i class="is-up"></i>상승</span><span><i class="is-down"></i>하락</span><span><i class="is-cash"></i>보합·현금</span><small>면적 = 포트폴리오 비중 · 등락 = 전일 대비(지연 시세)</small></div>`;
}

function expiresIn(value, now = Date.now()) {
  const end = value ? new Date(value).getTime() : NaN;
  if (!Number.isFinite(end)) return '-';
  const minutes = Math.round((end - now) / 60000);
  if (minutes <= 0) return '만료';
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  return days ? `${days}일 ${hours}시간` : `${hours}시간 ${minutes % 60}분`;
}

export function queueHtml({ board, loggedIn, filter = 'all' }, now = Date.now()) {
  if (!loggedIn) return '<div class="dash-empty"><p>로그인하면 오늘의 추천에 바로 매수·관망·제외를 기록할 수 있습니다.</p><button type="button" class="dash-btn is-primary" data-dash-login>로그인</button></div>';
  if (!board) return '<div class="dash-empty"><p>추천을 불러오는 중…</p></div>';
  let picks = picksOf(board);
  if (filter === 'waiting') picks = picks.filter((pick) => !(board.my_decisions?.[pick.signal_id] || []).length);
  if (filter === 'decided') picks = picks.filter((pick) => (board.my_decisions?.[pick.signal_id] || []).length);
  if (!picks.length) return `<div class="dash-empty"><p>${filter === 'all' ? '오늘 추천이 없습니다.' : '해당하는 추천이 없습니다.'}</p></div>`;
  return picks.map((pick, index) => `<article class="dash-queue-card">
    <header><span class="dash-rank">${index + 1}</span>${decisionBadge(board.my_decisions?.[pick.signal_id])}
      <span class="dash-queue-meta"><b>${esc(ITEM_SHORT[pick.item])}</b> ${esc(pick.rank)}위 · 만료 ${esc(expiresIn(board.expires_at, now))}</span></header>
    <p class="dash-queue-name"><span>${esc(pick.symbol)}</span><strong>${esc(pick.name)}</strong>${sectorSlot(pick.symbol)}</p>
    <p class="dash-queue-price">${fmt(pick.close)}<small>기준일 ${esc(board.as_of || '')} 종가</small></p>
    <p class="dash-queue-live">${liveSlot(pick.symbol, pick.market)}</p>
    <div class="dash-queue-risk">${riskSlot(pick.symbol, { full: true })}</div>
    ${exitPlanHtml(pick.exit_plan)}
    <div class="dash-chips"><span>추천 근거</span>${(pick.reason_labels || []).filter(Boolean).slice(0, 3).map((label) => `<i>${esc(label)}</i>`).join('')}</div>
    ${decisionForm(pick)}
  </article>`).join('');
}

export function sparklinePath(closes, width = 120, height = 36) {
  const values = closes.filter(num).map(Number);
  if (values.length < 2) return '';
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return values.map((value, index) => `${index ? 'L' : 'M'}${(index / (values.length - 1) * width).toFixed(1)},${(height - (value - min) / span * (height - 2) - 1).toFixed(1)}`).join(' ');
}

export function flowCardHtml(market, quote, series) {
  const closes = (series?.ohlcv || []).map((bar) => bar.c);
  const first = closes.find(num);
  const last = [...closes].reverse().find(num);
  const periodReturn = num(first) && num(last) && first ? (last / first - 1) * 100 : null;
  const highs = (series?.ohlcv || []).map((bar) => bar.h).filter(num);
  const lows = (series?.ohlcv || []).map((bar) => bar.l).filter(num);
  const ok = quote && quote.status === 'ok';
  const path = sparklinePath(closes);
  return `<article class="dash-flow-card">
    <h3>${esc(market.name)}</h3>
    <p class="dash-flow-value ${tone(ok ? quote.change_pct : null)}">${ok ? fmt(quote.value, market.digits) : '-'}</p>
    <p class="dash-flow-change ${tone(ok ? quote.change_pct : null)}">${ok ? `${signed(quote.change_pct)} <small>전일 대비</small>` : '<small>시세 조회 실패</small>'}</p>
    <svg class="dash-spark ${tone(periodReturn)}" viewBox="0 0 120 36" preserveAspectRatio="none" aria-hidden="true">${path ? `<path d="${path}"/>` : ''}</svg>
    <dl><div><dt>기간 등락</dt><dd class="${tone(periodReturn)}">${signed(periodReturn)}</dd></div>
      <div><dt>기간 고가</dt><dd>${highs.length ? fmt(Math.max(...highs), market.digits) : '-'}</dd></div>
      <div><dt>기간 저가</dt><dd>${lows.length ? fmt(Math.min(...lows), market.digits) : '-'}</dd></div></dl>
    ${series?.error ? `<p class="dash-note">${esc(series.error)}</p>` : ''}
  </article>`;
}

export function movingAverage(closes, window) {
  return closes.map((_, index) => {
    if (index + 1 < window) return null;
    const slice = closes.slice(index + 1 - window, index + 1);
    return slice.every(num) ? slice.reduce((sum, value) => sum + Number(value), 0) / window : null;
  });
}

export function systemRows({ refresh, health, outcomes, quotesAt, loggedIn }, now = Date.now()) {
  const rows = [];
  const kospi = refresh?.core_cache?.['^KS11']?.latest;
  const ageDays = kospi ? (now - new Date(`${kospi}T15:30:00+09:00`).getTime()) / 86400000 : null;
  rows.push(['국내 지수 일봉', refresh ? (num(ageDays) && ageDays < 4 ? 'ok' : 'warn') : 'unknown', kospi ? `${kospi} 기준` : '-']);
  rows.push(['해외 지수·환율', refresh ? ((refresh.stale_tickers || []).length ? 'warn' : 'ok') : 'unknown',
    refresh && (refresh.stale_tickers || []).length ? `지연 ${refresh.stale_tickers.length}개` : '정상 갱신']);
  rows.push(['지연 시세 조회', quotesAt ? 'ok' : 'unknown', quotesAt ? kstTime(quotesAt) : '-']);
  if (loggedIn) {
    rows.push(['KQS 추천 게시', health ? ((health.alerts || []).some((a) => a.code === 'publication_missing') ? 'error' : 'ok') : 'unknown',
      health?.published_session ? `${health.published_session} 기준` : '-']);
    rows.push(['KQS 엔진 실행', health ? (['published', 'already_published'].includes(health.last_run_status) ? 'ok' : 'warn') : 'unknown',
      health?.last_run_finished_at ? kstTime(health.last_run_finished_at, true) : '-']);
    rows.push(['추천 성과 집계', outcomes ? (outcomes.available ? 'ok' : 'warn') : 'unknown', outcomes?.through ? `${outcomes.through} 기준` : '-']);
  }
  return rows;
}

const STATUS_TEXT = { ok: '정상', warn: '주의', error: '오류', unknown: '확인 중' };

// ── 화면 ─────────────────────────────────────────────────

export function dashboardView(container) {
  const loggedIn = isAuthenticated();
  const state = { loggedIn, board: null, outcomes: null, insight: null, error: null, quotes: {}, quotesAt: null,
    refresh: null, flowPeriod: '1mo', chartMarket: 'kospi', chartFrame: 'day', briefTab: 'news', news: null, queueFilter: 'all' };
  const charts = [];
  let alive = true;
  window._viewCleanup = () => { alive = false; charts.splice(0).forEach((chart) => { try { chart.destroy(); } catch { /* 이미 해제 */ } }); };

  container.innerHTML = `<div class="dash">
    <section class="dash-card dash-greet" data-dash-greet></section>
    <section class="dash-card dash-brief">
      <header class="dash-head"><h2>오늘의 투자 브리핑</h2>
        <div class="dash-tabs" role="tablist">
          <button type="button" role="tab" data-brief-tab="news">보유종목 뉴스 <b data-brief-count></b></button>
          <button type="button" role="tab" data-brief-tab="sells">매도 신호 <b data-sell-count></b></button>
          <button type="button" role="tab" data-brief-tab="outcomes">추천 성과</button>
          <button type="button" role="tab" data-brief-tab="health">엔진 상태</button>
        </div></header>
      <div class="dash-brief-sub"><h3>내 보유종목 주요 뉴스</h3><span class="dash-kqs" data-brief-badge><i class="fa-regular fa-newspaper"></i> 뉴스</span></div>
      <p class="dash-note" data-brief-note>내 자산의 보유 비중이 높은 종목 뉴스부터 보여 줍니다. 헤드라인을 누르면 새 창에서 원문이 열립니다.</p>
      <div data-dash-brief></div>
    </section>
    <section class="dash-card dash-assets">
      <header class="dash-head"><h2>내 자산</h2><span class="dash-total" data-dash-total></span>
        <button type="button" class="dash-link" data-dash-go="portfolio-holdings">자세히 보기 <i class="fa-solid fa-arrow-right"></i></button></header>
      <div data-dash-assets></div>
    </section>
    <section class="dash-card dash-queue">
      <header class="dash-head"><h2>KQS 의사결정 큐 <b class="dash-count" data-queue-count>0</b></h2>
        <select class="dash-select" data-queue-filter aria-label="큐 필터"><option value="all">전체 신호</option><option value="waiting">판단 대기</option><option value="decided">기록 완료</option></select></header>
      <div class="dash-queue-list" data-dash-queue></div>
    </section>
    <section class="dash-card dash-flow">
      <header class="dash-head"><h2>시장 흐름</h2><span class="dash-sub">주요 지수의 최근 흐름을 한눈에 확인하세요.</span>
        <div class="dash-seg" data-flow-periods>${FLOW_PERIODS.map(([key, label]) => `<button type="button" data-flow-period="${key}">${label}</button>`).join('')}</div>
        <button type="button" class="dash-link" data-dash-go="world-markets">더보기 <i class="fa-solid fa-arrow-right"></i></button></header>
      <div class="dash-flow-grid" data-dash-flow>${MARKETS.map((market) => flowCardHtml(market, null, null)).join('')}</div>
    </section>
    <section class="dash-card dash-chart">
      <header class="dash-head"><h2><select class="dash-select is-title" data-chart-market aria-label="차트 지수">${MARKETS.map((market) => `<option value="${market.id}">${esc(market.name)} 차트</option>`).join('')}</select></h2>
        <div class="dash-seg" data-chart-frames>${CHART_FRAMES.map(([key, label]) => `<button type="button" data-chart-frame="${key}">${label}</button>`).join('')}</div></header>
      <div class="dash-chart-legend"><span><i style="background:#facc15"></i>MA5 <b data-ma="5">-</b></span><span><i style="background:#3b82f6"></i>MA20 <b data-ma="20">-</b></span><span><i style="background:#ec4899"></i>MA60 <b data-ma="60">-</b></span></div>
      <div class="dash-chart-price" data-chart-price></div><div class="dash-chart-volume" data-chart-volume></div>
      <p class="dash-note" data-chart-note></p>
    </section>
    <section class="dash-card dash-system">
      <header class="dash-head"><h2>데이터 및 시스템</h2><button type="button" class="dash-link" data-dash-go="server-resources">더보기 <i class="fa-solid fa-arrow-right"></i></button></header>
      <p class="dash-note" data-system-stamp>데이터 확인 중…</p>
      <ul class="dash-system-list" data-dash-system></ul>
      <div class="dash-manual"><i class="fa-solid fa-hand-pointer" aria-hidden="true"></i><span>자동 주문 없음 · 매수·매도는 직접 기록합니다</span>
        <button type="button" data-dash-go="my-strategies">기록하러 가기</button></div>
    </section>
  </div>`;

  const $ = (selector) => container.querySelector(selector);
  const bindCommon = (root) => {
    root.querySelectorAll('[data-dash-login]').forEach((button) => button.addEventListener('click', openAuthModal));
    root.querySelectorAll('[data-dash-go]').forEach((button) => button.addEventListener('click', () => window.navigate(button.dataset.dashGo)));
  };
  bindCommon(container);

  const renderGreeting = () => { $('[data-dash-greet]').innerHTML = greetingHtml(new Date(), currentUserInfo()?.username || ''); };
  const renderBrief = () => {
    container.querySelectorAll('[data-brief-tab]').forEach((tab) => tab.setAttribute('aria-selected', String(tab.dataset.briefTab === state.briefTab)));
    const titles = { news: '내 보유종목 주요 뉴스', sells: '내 보유 종목 매도 신호', outcomes: '추천 성과(전향 관측)', health: '엔진 상태' };
    $('.dash-brief-sub h3').textContent = titles[state.briefTab];
    $('[data-brief-note]').hidden = state.briefTab !== 'news';
    $('[data-brief-badge]').hidden = state.briefTab !== 'news';
    const el = $('[data-dash-brief]');
    el.innerHTML = briefingHtml(state.briefTab, state);
    bindCommon(el);
    fillSectorBadges(el);
    fillRiskBadges(el);
    startLiveQuotes(el);
    $('[data-brief-count]').textContent = state.news?.items?.length ? `(${state.news.items.length})` : '';
    const sells = (state.board?.sell_signals || []).filter((row) => row.state === 'exit_signal').length;
    $('[data-sell-count]').textContent = sells ? `(${sells})` : '';
  };
  const renderAssets = () => {
    const el = $('[data-dash-assets]');
    el.innerHTML = assetsHtml(state);
    bindCommon(el);
    $('[data-dash-total]').innerHTML = state.insight ? `총 자산 <b>${fmt(state.insight.total)}원</b>${state.partialNote ? ` <small>(${esc(state.partialNote)})</small>` : ''}` : '';
  };
  const renderQueue = () => {
    const el = $('[data-dash-queue]');
    el.innerHTML = queueHtml({ board: state.board, loggedIn: state.loggedIn, filter: state.queueFilter });
    bindCommon(el);
    el.querySelectorAll('[data-sb-decision]').forEach((form) => bindDecisionForm(form, loadBoard));
    fillSectorBadges(el);
    fillRiskBadges(el);
    startLiveQuotes(el);
    $('[data-queue-count]').textContent = String(picksOf(state.board).length);
  };
  const renderSystem = () => {
    $('[data-dash-system]').innerHTML = systemRows({ refresh: state.refresh, health: state.board?.health, outcomes: state.outcomes,
      quotesAt: state.quotesAt, loggedIn: state.loggedIn }).map(([label, status, detail]) => `<li>
        <span>${esc(label)}</span><span class="dash-status is-${status}"><i></i>${STATUS_TEXT[status]}</span><time>${esc(detail)}</time></li>`).join('');
    if (state.quotesAt) {
      const minutes = Math.max(0, Math.round((Date.now() - new Date(state.quotesAt).getTime()) / 60000));
      $('[data-system-stamp]').textContent = `시세 최종 조회 ${kstTime(state.quotesAt)} (${minutes}분 전) · 시세는 15분 지연`;
    }
  };

  async function loadBoard() {
    if (!state.loggedIn) return;
    try { state.board = await authRequest('/api/kqs/strategy-board'); } catch (error) { state.board = { available: false, detail: error.message, items: [] }; }
    if (!alive) return;
    renderGreeting(); renderBrief(); renderQueue(); renderSystem();
  }
  async function loadNews() {
    if (!state.loggedIn || !alive) return;
    try { state.news = await authRequest('/api/portfolio/news'); } catch (error) { state.news = { error: error.message }; }
    if (!alive) return;
    if (state.briefTab === 'news') renderBrief(); else $('[data-brief-count]').textContent = state.news?.items?.length ? `(${state.news.items.length})` : '';
    // 서버 캐시는 10분 주기로 갱신된다. 수집 대기 종목이 있으면 1분 뒤, 아니면 5분 뒤 캐시를 다시 읽는다.
    setTimeout(loadNews, (state.news?.pending?.length ? 60 : 300) * 1000);
  }
  async function loadOutcomes() {
    if (!state.loggedIn) return;
    try { state.outcomes = await authRequest('/api/kqs/recommendation-outcomes'); } catch (error) { state.outcomes = { available: false, detail: error.message }; }
    if (!alive) return;
    if (state.briefTab === 'outcomes') renderBrief();
    renderSystem();
  }
  async function loadAssets() {
    if (!state.loggedIn) return;
    try {
      const portfolio = await authRequest('/api/portfolio');
      const valuation = await portfolioValuation(portfolio);
      const basis = weightBasis(portfolio, valuation);
      state.insight = portfolioInsights(portfolio, basis.valuation);
      state.partialNote = basis.partial ? `시세 확인된 ${basis.priced}개 기준 · ${basis.excluded}개 제외` : '';
    } catch (error) { state.error = error.message; }
    if (alive) renderAssets();
  }
  async function loadQuotes() {
    try {
      const response = await fetch('/api/market/snapshot', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tickers: MARKETS.map((market) => market.ticker) }) });
      const data = await response.json();
      state.quotes = Object.fromEntries((data.items || []).map((item) => [item.ticker, item]));
      state.quotesAt = data.fetched_at;
    } catch { state.quotes = {}; }
    if (alive) { renderFlow(); renderSystem(); }
  }
  const flowSeries = {};
  function renderFlow() {
    $('[data-dash-flow]').innerHTML = MARKETS.map((market) => flowCardHtml(market, state.quotes[market.ticker], flowSeries[market.id])).join('');
    container.querySelectorAll('[data-flow-period]').forEach((button) => button.classList.toggle('is-active', button.dataset.flowPeriod === state.flowPeriod));
  }
  async function loadFlow() {
    const period = state.flowPeriod;
    await Promise.all(MARKETS.map(async (market) => {
      const query = period === '1w' ? '1mo' : period;
      try {
        const response = await fetch(`/api/home/market-candle?market=${market.id}&period=${query}`);
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.reason || '데이터 없음');
        const ohlcv = data.ohlcv || [];
        const shown = period === '1w' ? ohlcv.slice(-5) : data.display_from ? ohlcv.filter((bar) => String(bar.date) >= data.display_from) : ohlcv;
        flowSeries[market.id] = { ohlcv: shown.length ? shown : ohlcv };
      } catch (error) { flowSeries[market.id] = { ohlcv: [], error: `차트 데이터 없음: ${error.message}` }; }
    }));
    if (alive && period === state.flowPeriod) renderFlow();
  }
  async function loadChart() {
    const market = MARKETS.find((item) => item.id === state.chartMarket);
    container.querySelectorAll('[data-chart-frame]').forEach((button) => button.classList.toggle('is-active', button.dataset.chartFrame === state.chartFrame));
    const query = state.chartFrame === 'day' ? 'period=6mo' : state.chartFrame === 'week' ? 'timeframe=1wk' : 'timeframe=1mo';
    const note = $('[data-chart-note]');
    charts.splice(0).forEach((chart) => { try { chart.destroy(); } catch { /* 이미 해제 */ } });
    $('[data-chart-price]').innerHTML = ''; $('[data-chart-volume]').innerHTML = '';
    try {
      const response = await fetch(`/api/home/market-candle?market=${market.id}&${query}`);
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.reason || '데이터 없음');
      if (!alive || typeof ApexCharts === 'undefined') return;
      const bars = (data.ohlcv || []).filter((bar) => [bar.o, bar.h, bar.l, bar.c].every(num));
      const closes = bars.map((bar) => bar.c);
      const ma = { 5: movingAverage(closes, 5), 20: movingAverage(closes, 20), 60: movingAverage(closes, 60) };
      Object.entries(ma).forEach(([key, values]) => { const last = [...values].reverse().find(num); container.querySelector(`[data-ma="${key}"]`).textContent = num(last) ? fmt(last, 2) : '-'; });
      const x = (bar) => new Date(String(bar.date).length === 10 ? `${bar.date}T00:00:00+09:00` : bar.date).getTime();
      const common = { chart: { background: 'transparent', foreColor: '#8a97ad', toolbar: { show: false }, zoom: { enabled: false }, animations: { enabled: false } },
        grid: { borderColor: '#1e2a3f', strokeDashArray: 3 }, theme: { mode: 'dark' }, xaxis: { type: 'datetime', labels: { datetimeUTC: false } } };
      const price = new ApexCharts($('[data-chart-price]'), { ...common,
        chart: { ...common.chart, type: 'candlestick', height: 250, id: 'dash-price' },
        series: [{ name: market.name, type: 'candlestick', data: bars.map((bar) => ({ x: x(bar), y: [bar.o, bar.h, bar.l, bar.c] })) },
          ...[['MA5', 5, '#facc15'], ['MA20', 20, '#3b82f6'], ['MA60', 60, '#ec4899']].map(([name, key]) => ({ name, type: 'line', data: bars.map((bar, i) => ({ x: x(bar), y: ma[key][i] === null ? null : Number(ma[key][i].toFixed(2)) })) }))],
        colors: ['#ef4444', '#facc15', '#3b82f6', '#ec4899'], legend: { show: false },
        stroke: { width: [1, 1.8, 1.8, 1.8], colors: ['#8a97ad', '#facc15', '#3b82f6', '#ec4899'], curve: 'straight' },
        fill: { opacity: [1, 1, 1, 1] },
        plotOptions: { candlestick: { colors: { upward: '#ef4444', downward: '#3b82f6' }, wick: { useFillColor: true } } },
        xaxis: { ...common.xaxis, labels: { show: false } },
        yaxis: { opposite: true, min: Math.min(...bars.map((bar) => bar.l)) * 0.985, max: Math.max(...bars.map((bar) => bar.h)) * 1.015,
          tickAmount: 5, labels: { formatter: (value) => fmt(value, 0) } },
        tooltip: { shared: true, x: { format: 'yyyy-MM-dd' } } });
      const volume = new ApexCharts($('[data-chart-volume]'), { ...common,
        chart: { ...common.chart, type: 'bar', height: 90, id: 'dash-volume' },
        series: [{ name: '거래량', data: bars.map((bar, i) => ({ x: x(bar), y: bar.v || 0, fillColor: i && bar.c < bars[i - 1].c ? '#3b82f6' : '#ef4444' })) }],
        plotOptions: { bar: { columnWidth: '70%' } }, dataLabels: { enabled: false },
        yaxis: { opposite: true, labels: { formatter: (value) => (value >= 1e6 ? `${(value / 1e6).toFixed(1)}M` : fmt(value, 0)) } },
        tooltip: { x: { format: 'yyyy-MM-dd' } } });
      charts.push(price, volume);
      await price.render(); await volume.render();
      note.textContent = `${market.name} · ${{ day: '일봉(6개월)', week: '주봉(5년)', month: '월봉(10년)' }[state.chartFrame]} · 상승 빨강 · 하락 파랑 · 지연 시세`;
    } catch (error) {
      note.textContent = `차트 데이터 없음: ${error.message}`;
    }
  }
  async function loadRefresh() {
    try { state.refresh = await (await fetch('/api/market/refresh-status')).json(); } catch { state.refresh = null; }
    if (alive) renderSystem();
  }

  container.querySelectorAll('[data-brief-tab]').forEach((tab) => tab.addEventListener('click', () => {
    state.briefTab = tab.dataset.briefTab; renderBrief();
    if (state.briefTab === 'outcomes' && !state.outcomes) loadOutcomes();
  }));
  $('[data-queue-filter]').addEventListener('change', (event) => { state.queueFilter = event.target.value; renderQueue(); });
  container.querySelectorAll('[data-flow-period]').forEach((button) => button.addEventListener('click', () => { state.flowPeriod = button.dataset.flowPeriod; renderFlow(); loadFlow(); }));
  container.querySelectorAll('[data-chart-frame]').forEach((button) => button.addEventListener('click', () => { state.chartFrame = button.dataset.chartFrame; loadChart(); }));
  $('[data-chart-market]').addEventListener('change', (event) => { state.chartMarket = event.target.value; loadChart(); });

  renderGreeting(); renderBrief(); renderAssets(); renderQueue(); renderFlow(); renderSystem();
  loadBoard(); loadNews(); loadOutcomes(); loadAssets(); loadQuotes(); loadFlow(); loadChart(); loadRefresh();
}
