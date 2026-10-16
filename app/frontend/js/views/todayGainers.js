const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
const signedPct = (v) => (Number.isFinite(v) ? `${v > 0 ? '+' : ''}${v.toFixed(2)}%` : '-');

/** EINT-049: 전체 종목의 상승·하락 수와 등락률 중간값(테마별). 응답에 summary가 없으면 빈 문자열. */
export function summaryMarkup(data) {
  const s = data.summary;
  if (!s) return '';
  const themes = Object.entries(s.by_theme || {}).map(([name, t]) => `<span>${esc(name)} ${t.count}종목 중간값 <b class="${t.median_change_pct >= 0 ? 'is-up' : 'is-down'}">${signedPct(t.median_change_pct)}</b></span>`).join('');
  return `<div class="gainers-summary"><span>상승 <b class="is-up">${s.up}</b> · 하락 <b class="is-down">${s.down}</b> · 보합 ${s.flat}</span>
    <span>등락률 중간값 <b class="${s.median_change_pct >= 0 ? 'is-up' : 'is-down'}">${signedPct(s.median_change_pct)}</b></span>${themes}</div>`;
}

function formatPrice(value) {
  if (!Number.isFinite(value)) return '--';
  return `${Math.round(value).toLocaleString()}원`;
}

function rowMarkup(item) {
  const isUp = item.change_pct >= 0;
  const rankClass = item.rank <= 3 ? ` is-top${item.rank}` : '';
  return `
    <button type="button" class="gainers-row${rankClass}" data-quote-ticker="${esc(item.ticker)}" aria-haspopup="dialog" aria-label="${esc(item.name)} 시세 보기">
      <span class="gainers-rank">${item.rank}</span>
      <div class="gainers-name">
        <strong>${esc(item.name)}</strong>
        <span class="gainers-meta">${esc(item.ticker)} · ${esc(item.sector)}</span>
        <span class="gainers-quote-action"><i class="fa-solid fa-chart-line" aria-hidden="true"></i> 시세 보기</span>
      </div>
      <span class="gainers-price">${formatPrice(item.price)}</span>
      <span class="gainers-change ${isUp ? 'is-up' : 'is-down'}">${isUp ? '▲' : '▼'} ${Math.abs(item.change_pct).toFixed(2)}%</span>
    </button>`;
}

function installQuoteModal(container) {
  const modal = document.createElement('div');
  modal.className = 'gainers-quote-modal-backdrop';
  modal.hidden = true;
  modal.innerHTML = `
    <section class="gainers-quote-modal" role="dialog" aria-modal="true" aria-labelledby="gainers-quote-title" tabindex="-1">
      <header class="gainers-quote-modal-header">
        <div>
          <p id="gainers-quote-sector"></p>
          <h2 id="gainers-quote-title">--</h2>
          <div class="gainers-quote-price-row"><strong id="gainers-quote-price">--</strong><em id="gainers-quote-change">--</em></div>
        </div>
        <button type="button" class="gainers-quote-modal-close" aria-label="시세 창 닫기"><i class="fa-solid fa-xmark"></i></button>
      </header>
      <div class="gainers-quote-periods" aria-label="차트 기간">
        <button type="button" data-quote-period="1mo">1개월</button>
        <button type="button" data-quote-period="3mo" class="active">3개월</button>
        <button type="button" data-quote-period="1y">1년</button>
      </div>
      <div class="gainers-quote-chart-wrap">
        <div id="gainers-quote-chart" class="gainers-quote-chart"></div>
        <div id="gainers-quote-loading" class="gainers-quote-loading"><i class="fa-solid fa-spinner fa-spin"></i> 시세를 불러오는 중…</div>
      </div>
      <footer id="gainers-quote-source" class="gainers-quote-source"></footer>
    </section>`;
  document.body.appendChild(modal);

  const panel = modal.querySelector('.gainers-quote-modal');
  const title = modal.querySelector('#gainers-quote-title');
  const sector = modal.querySelector('#gainers-quote-sector');
  const price = modal.querySelector('#gainers-quote-price');
  const change = modal.querySelector('#gainers-quote-change');
  const chartEl = modal.querySelector('#gainers-quote-chart');
  const loading = modal.querySelector('#gainers-quote-loading');
  const source = modal.querySelector('#gainers-quote-source');
  const closeButton = modal.querySelector('.gainers-quote-modal-close');
  let chart = null;
  let abortController = null;
  let selected = null;
  let lastFocused = null;
  let requestId = 0;

  const destroyChart = () => {
    if (chart) { try { chart.destroy(); } catch {} chart = null; }
    chartEl.replaceChildren();
  };
  const close = () => {
    requestId += 1;
    modal.hidden = true;
    abortController?.abort();
    destroyChart();
    document.body.classList.remove('modal-open');
    lastFocused?.focus();
  };
  const loadChart = async (period = '3mo') => {
    if (!selected) return;
    const currentRequest = ++requestId;
    abortController?.abort();
    abortController = new AbortController();
    destroyChart();
    loading.hidden = false;
    loading.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 시세를 불러오는 중…';
    source.textContent = '';
    modal.querySelectorAll('[data-quote-period]').forEach((button) => button.classList.toggle('active', button.dataset.quotePeriod === period));
    try {
      const response = await fetch(`/api/home/market-candle?market=stock&period=${period}&interval=1d&ticker=${encodeURIComponent(selected.ticker)}`, { signal: abortController.signal });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `HTTP ${response.status}`);
      const data = await response.json();
      const ohlcv = data.ohlcv || [];
      if (!ohlcv.length) throw new Error('데이터 없음');
      if (currentRequest !== requestId || modal.hidden) return;
      const last = ohlcv.at(-1);
      const previous = ohlcv.length > 1 ? ohlcv.at(-2).c : last.o;
      const changePct = previous ? ((last.c / previous) - 1) * 100 : 0;
      const isUp = changePct >= 0;
      price.textContent = formatPrice(last.c);
      change.textContent = `${isUp ? '▲' : '▼'} ${Math.abs(changePct).toFixed(2)}%`;
      change.className = isUp ? 'is-up' : 'is-down';
      if (!window.ApexCharts) throw new Error('차트 라이브러리 없음');
      chart = new window.ApexCharts(chartEl, {
        chart: { type: 'candlestick', height: 390, toolbar: { show: true }, fontFamily: 'Pretendard, sans-serif' },
        series: [{ data: ohlcv.map((point) => ({ x: new Date(point.date).getTime(), y: [point.o, point.h, point.l, point.c] })) }],
        plotOptions: { candlestick: { colors: { upward: '#e11d48', downward: '#2563eb' } } },
        xaxis: { type: 'datetime' },
        yaxis: { tooltip: { enabled: true }, labels: { formatter: (value) => Math.round(value).toLocaleString() } },
        grid: { borderColor: '#e2e8f0' },
        tooltip: { x: { format: 'yyyy-MM-dd' } },
      });
      await chart.render();
      if (currentRequest !== requestId || modal.hidden) return;
      source.textContent = 'Yahoo Finance · 장중 시세는 약 15분 지연될 수 있습니다.';
      loading.hidden = true;
    } catch (error) {
      if (error.name === 'AbortError' || currentRequest !== requestId || modal.hidden) return;
      loading.innerHTML = `<span>시세를 불러오지 못했습니다: ${error.message}</span>`;
    }
  };
  const open = (item, trigger) => {
    selected = item;
    lastFocused = trigger;
    title.textContent = `${item.name} · ${item.ticker}`;
    sector.textContent = item.sector;
    price.textContent = formatPrice(item.price);
    const isUp = item.change_pct >= 0;
    change.textContent = `${isUp ? '▲' : '▼'} ${Math.abs(item.change_pct).toFixed(2)}%`;
    change.className = isUp ? 'is-up' : 'is-down';
    modal.hidden = false;
    document.body.classList.add('modal-open');
    panel.focus();
    loadChart();
  };

  closeButton.addEventListener('click', close);
  modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !modal.hidden) close(); });
  modal.querySelector('.gainers-quote-periods').addEventListener('click', (event) => {
    const button = event.target.closest('[data-quote-period]');
    if (button) loadChart(button.dataset.quotePeriod);
  });
  return { open, destroy: () => { abortController?.abort(); destroyChart(); modal.remove(); } };
}

export function marketRankingsView(container, {
  id,
  title,
  icon,
  description,
  endpoint,
  loadingText,
  note,
  allToggle = false,
  pageSize = 20,
}) {
  container.innerHTML = `
    <section class="gainers-page">
      <header class="gainers-head">
        <div>
          <h1><i class="fa-solid ${icon}"></i> ${title}</h1>
          <p>${description}</p>
        </div>
        <div class="gainers-actions">
          <span id="${id}-stamp">조회 전</span>
          ${allToggle ? `<button type="button" id="${id}-all" aria-pressed="false"><i class="fa-solid fa-list"></i> <span>전체 보기</span></button>` : ''}
          <button type="button" id="${id}-refresh"><i class="fa-solid fa-rotate-right"></i> 새로고침</button>
        </div>
      </header>
      <div id="${id}-summary"></div>
      <div class="gainers-list" id="${id}-list">
        <div class="gainers-loading"><i class="fa-solid fa-spinner fa-spin"></i> ${loadingText}</div>
      </div>
      <p class="gainers-note">${note}</p>
    </section>`;

  let disposed = false;
  let showAll = false;
  const quoteModal = installQuoteModal(container);
  const itemsByTicker = new Map();

  async function loadGainers() {
    const list = container.querySelector(`#${id}-list`);
    const stamp = container.querySelector(`#${id}-stamp`);
    const button = container.querySelector(`#${id}-refresh`);
    button.disabled = true;
    button.classList.add('is-loading');
    stamp.textContent = '조회 중…';

    try {
      const response = await fetch(`${endpoint}?limit=${showAll ? 0 : pageSize}`);
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `HTTP ${response.status}`);
      const data = await response.json();
      if (disposed) return;
      const items = data.items || [];
      const excluded = data.excluded || [];
      if (!items.length) {
        const reason = excluded.length ? `(${excluded.length}종목 시세 조회 실패)` : '';
        throw new Error(`데이터 없음 ${reason}`.trim());
      }

      itemsByTicker.clear();
      items.forEach((item) => itemsByTicker.set(item.ticker, item));
      list.innerHTML = items.map(rowMarkup).join('');
      const summary = container.querySelector(`#${id}-summary`);
      if (summary) summary.innerHTML = summaryMarkup(data);
      const allButton = container.querySelector(`#${id}-all`);
      if (allButton) {
        allButton.querySelector('span').textContent = showAll ? `상위 ${pageSize}개만` : `전체 ${data.universe_size ?? ''}종목 보기`;
        allButton.setAttribute('aria-pressed', String(showAll));
      }
      const fetchedAt = data.fetched_at ? new Date(data.fetched_at) : null;
      const stampText = fetchedAt && !Number.isNaN(fetchedAt.valueOf())
        ? `조회 ${fetchedAt.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}`
        : '조회 완료';
      const basis = `${data.summary ? '전체' : '표본'} ${data.universe_size ?? '-'}종목 기준`;
      stamp.textContent = [stampText, basis, data.stale ? '지연(최근 값)' : '', excluded.length ? `${excluded.length}종목 제외` : ''].filter(Boolean).join(' · ');
    } catch (error) {
      if (disposed) return;
      list.innerHTML = `<div class="gainers-error"><i class="fa-solid fa-triangle-exclamation"></i> 데이터를 불러오지 못했습니다: ${error.message}</div>`;
      stamp.textContent = '조회 실패';
    } finally {
      if (!disposed) {
        button.disabled = false;
        button.classList.remove('is-loading');
      }
    }
  }

  container.querySelector(`#${id}-refresh`).addEventListener('click', loadGainers);
  container.querySelector(`#${id}-all`)?.addEventListener('click', () => { showAll = !showAll; loadGainers(); });
  container.querySelector(`#${id}-list`).addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-quote-ticker]');
    const item = trigger && itemsByTicker.get(trigger.dataset.quoteTicker);
    if (item) quoteModal.open(item, trigger);
  });
  loadGainers();

  window._viewCleanup = () => { disposed = true; quoteModal.destroy(); };
}

const MARKETS = [['KOSPI', '코스피'], ['KOSDAQ', '코스닥']];
const REFRESH_MS = 30_000;
const escapeText = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const eok = (krw) => (Number.isFinite(krw) ? `${Math.round(krw / 1e8).toLocaleString('ko-KR')}억` : '-');

export function moverRowMarkup(item) {
  const isUp = item.change_pct >= 0;
  const rankClass = item.rank <= 3 ? ` is-top${item.rank}` : '';
  return `
    <button type="button" class="gainers-row${rankClass}" data-quote-ticker="${escapeText(item.ticker)}" aria-haspopup="dialog" aria-label="${escapeText(item.name)} 시세 보기">
      <span class="gainers-rank">${item.rank}</span>
      <div class="gainers-name">
        <strong>${escapeText(item.name)}${item.limit_up ? ' <em class="gainers-limit">상한가</em>' : ''}</strong>
        <span class="gainers-meta">${escapeText(item.code)} · 거래대금 ${eok(item.trading_value_krw)} · 시총 ${eok(item.market_cap_krw)}</span>
      </div>
      <span class="gainers-price">${formatPrice(item.price)}</span>
      <span class="gainers-change ${isUp ? 'is-up' : 'is-down'}">${isUp ? '▲' : '▼'} ${Math.abs(item.change_pct).toFixed(2)}%</span>
    </button>`;
}

export function moverStampText(data, now = new Date()) {
  const asOf = data.as_of ? new Date(data.as_of) : null;
  const time = asOf && !Number.isNaN(asOf.valueOf())
    ? asOf.toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hour12: false }) : '--:--';
  const status = data.market_status === 'OPEN' ? '장중' : data.market_status === 'CLOSE' ? '장 마감' : '';
  return [status, `${time} 체결 기준`, data.stale ? '최신 갱신 실패 · 이전 값' : ''].filter(Boolean).join(' · ');
}

export function todayGainersView(container) {
  container.innerHTML = `
    <section class="gainers-page is-wide">
      <header class="gainers-head">
        <div>
          <h1><i class="fa-solid fa-arrow-trend-up"></i> 금일 상승종목 TOP 20</h1>
          <p>코스피·코스닥 <b>전 종목</b>(ETF·ETN 제외) 중 오늘 등락률이 높은 순서입니다. 장중에는 ${REFRESH_MS / 1000}초마다 자동으로 갱신됩니다.</p>
        </div>
        <div class="gainers-actions">
          <label class="gainers-filter"><input type="checkbox" id="gainers-liquid"> 거래대금 10억 이상만</label>
          <span id="gainers-stamp">조회 전</span>
          <button type="button" id="gainers-refresh"><i class="fa-solid fa-rotate-right"></i> 새로고침</button>
        </div>
      </header>
      <div class="gainers-markets">
        ${MARKETS.map(([market, label]) => `<section class="gainers-market" data-market="${market}">
          <header><h2>${label}</h2><small data-market-stamp>조회 중…</small></header>
          <div class="gainers-list" data-market-list><div class="gainers-loading"><i class="fa-solid fa-spinner fa-spin"></i> ${label} 상승 순위를 불러오는 중…</div></div>
        </section>`).join('')}
      </div>
      <p class="gainers-note">출처: 네이버 증권(실시간 시세가 아닐 수 있으며 몇 분 지연될 수 있음). 서버에서 시장별 30초 단위로 갱신합니다. 상승률 상위라는 것이 좋은 투자 대상이라는 뜻은 아니며, 급등 이유(실적·공시·테마·거래 정지 해제 등)와 거래대금을 반드시 확인하세요.</p>
    </section>`;

  let disposed = false;
  let timer = null;
  const quoteModal = installQuoteModal(container);
  const itemsByTicker = new Map();
  const stamp = container.querySelector('#gainers-stamp');
  const button = container.querySelector('#gainers-refresh');
  const liquid = container.querySelector('#gainers-liquid');

  async function loadMarket(market) {
    const section = container.querySelector(`[data-market="${market}"]`);
    const list = section.querySelector('[data-market-list]');
    const marketStamp = section.querySelector('[data-market-stamp]');
    const minValue = liquid.checked ? 1_000_000_000 : 0;
    try {
      const response = await fetch(`/api/market/top-gainers?market=${market}&limit=20&min_trading_value_krw=${minValue}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      if (disposed) return null;
      (data.items || []).forEach((item) => itemsByTicker.set(item.ticker, item));
      list.innerHTML = (data.items || []).length ? data.items.map(moverRowMarkup).join('')
        : '<div class="gainers-loading">조건에 맞는 종목이 없습니다.</div>';
      marketStamp.textContent = moverStampText(data);
      return data;
    } catch (error) {
      if (disposed) return null;
      list.innerHTML = `<div class="gainers-error"><i class="fa-solid fa-triangle-exclamation"></i> ${escapeText(error.message)}</div>`;
      marketStamp.textContent = '조회 실패';
      return null;
    }
  }

  async function loadAll() {
    if (disposed) return;
    button.disabled = true;
    stamp.textContent = '갱신 중…';
    itemsByTicker.clear();
    const results = await Promise.all(MARKETS.map(([market]) => loadMarket(market)));
    if (disposed) return;
    button.disabled = false;
    const open = results.some((data) => data && data.market_status === 'OPEN');
    stamp.textContent = `마지막 조회 ${new Date().toLocaleTimeString('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}${open ? ` · ${REFRESH_MS / 1000}초마다 자동 갱신` : ' · 장 마감(자동 갱신 쉼)'}`;
    window.clearTimeout(timer);
    if (open) timer = window.setTimeout(() => { if (document.visibilityState === 'visible') loadAll(); else schedule(); }, REFRESH_MS);
  }
  const schedule = () => { window.clearTimeout(timer); timer = window.setTimeout(loadAll, REFRESH_MS); };
  const onVisible = () => { if (document.visibilityState === 'visible' && !disposed) loadAll(); };

  button.addEventListener('click', loadAll);
  liquid.addEventListener('change', loadAll);
  document.addEventListener('visibilitychange', onVisible);
  container.querySelector('.gainers-markets').addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-quote-ticker]');
    const item = trigger && itemsByTicker.get(trigger.dataset.quoteTicker);
    if (item) quoteModal.open({ ...item, sector: item.market === 'KOSPI' ? '코스피' : '코스닥' }, trigger);
  });
  loadAll();

  window._viewCleanup = () => {
    disposed = true;
    window.clearTimeout(timer);
    document.removeEventListener('visibilitychange', onVisible);
    quoteModal.destroy();
  };
}
