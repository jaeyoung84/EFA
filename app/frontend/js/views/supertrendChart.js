// 슈퍼트렌드 독립 차트 (설계: docs/handoff/codex/2026-10-19-supertrend-chart-design.md).
// 종목 차트를 보며 매수·보유·매도 판단에 참고하는 화면이다. 엔진 추천·청산·원장·주문과 연결하지 않는다.
// 계산은 서버(/api/chart/supertrend)가 전체 이력으로 하고, 화면은 표시 구간만 잘라 보여 준다.
import { attachStockSearch } from '../utils/stockSearch.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const num = (value) => value !== null && value !== undefined && Number.isFinite(Number(value));
const fmt = (value, digits = 2) => (num(value) ? Number(value).toLocaleString('ko-KR', { maximumFractionDigits: digits }) : '-');
const signed = (value, digits = 2, suffix = '%') => (num(value) ? `${value > 0 ? '+' : ''}${Number(value).toFixed(digits)}${suffix}` : '-');

const DEFAULTS = { atr: 10, factor: 3 };
const RANGES = { '6m': 126, '1y': 252, '3y': 756 };
const PREF_KEY = 'efa-supertrend-chart';
const COLORS = { up: '#22c55e', down: '#ff3b5c', candleUp: '#f0304a', candleDown: '#3b82f6', muted: '#8590a6', select: '#f0b90b' };

// 4절: 직전 확정 주봉 × 확정 일봉 조합의 사실 요약. 매수·매도 지시가 아니다.
const COMBO = {
  '-1|-1': ['중기·단기 상승 방향 일치', '진입 시점의 추세선 거리·직전 고점·거래량을 확인하세요.'],
  '-1|1': ['중기 상승 속 단기 약화', '눌림인지 추가 하락인지, 하락 전환일과 추세선 이탈을 확인하세요.'],
  '1|-1': ['중기 하락 속 단기 반등(역추세 반등)', '반등 지속과 직전 고점 회복 여부, 추격 거리를 확인하세요.'],
  '1|1': ['중기·단기 하락 방향 일치', '하락 추세 유지 여부를 보고, 아직 없는 상승 전환을 미리 확정하지 마세요.'],
};

function dirLabel(direction) {
  if (direction === -1) return '<span class="st-dir is-up">▲ 상승</span>';
  if (direction === 1) return '<span class="st-dir is-down">▼ 하락</span>';
  return '<span class="st-dir">계산 중</span>';
}
const flipLabel = (type) => (type === 'up' ? 'BUY·상승 전환' : 'SELL·하락 전환');

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREF_KEY) || '{}'); } catch { return {}; }
}
function savePrefs(prefs) {
  try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch { /* 저장 불가 브라우저: 이번 화면에서만 유지 */ }
}

export function supertrendChartView(container) {
  const prefs = loadPrefs();
  const state = {
    ticker: prefs.ticker || '005930.KS', name: prefs.name || '삼성전자',
    atr: Number(prefs.atr) || DEFAULTS.atr, factor: Number(prefs.factor) || DEFAULTS.factor,
    range: RANGES[prefs.range] ? prefs.range : '1y', showMa: Boolean(prefs.showMa), showLevels: Boolean(prefs.showLevels),
    showDi: false, data: null, selected: null, hovered: null,
  };
  let charts = [];
  let alive = true;
  let requestId = 0;

  container.innerHTML = `<div class="efa-page st-page" data-local-state="off">
    <header class="efa-page-head"><div><h1><i class="fa-solid fa-wave-square"></i> 슈퍼트렌드 차트</h1>
      <p>일봉 슈퍼트렌드 방향, 직전에 확정된 주봉 방향, 모든 확정 전환을 함께 봅니다. BUY·SELL은 종가가 확정된 뒤의 추세 전환 표시이며 체결·추천이 아닙니다.</p></div></header>

    <section class="dash-card st-controls">
      <form class="st-form" data-st-form>
        <label class="st-field st-search"><span>종목</span><input type="text" data-st-search placeholder="종목명 또는 티커 (예: 삼성전자, AAPL)" aria-label="종목 검색"></label>
        <label class="st-field"><span>ATR 기간</span><input type="number" data-st-atr min="1" max="100" step="1"></label>
        <label class="st-field"><span>ATR 배수</span><input type="number" data-st-factor min="0.1" max="20" step="0.1"></label>
        <button type="submit" class="dash-btn is-primary">적용</button>
        <button type="button" class="dash-btn" data-st-reset title="ATR 10 · 배수 3.0">기본값</button>
      </form>
      <div class="st-toolbar">
        <div class="dash-seg" role="group" aria-label="표시 구간">
          ${Object.keys(RANGES).map((key) => `<button type="button" data-st-range="${key}">${{ '6m': '6개월', '1y': '1년', '3y': '3년' }[key]}</button>`).join('')}
        </div>
        <label class="st-toggle"><input type="checkbox" data-st-ma> 이동평균 20·60·200</label>
        <label class="st-toggle"><input type="checkbox" data-st-levels> 직전 20일 고점·저점</label>
      </div>
      <p class="efa-error" role="status" data-st-error></p>
    </section>

    <section class="dash-card st-status" data-st-status><p class="efa-muted">불러오는 중…</p></section>

    <section class="dash-card st-chart-card">
      <div class="st-chart-head"><strong data-st-title></strong><span class="dash-note" data-st-legend></span></div>
      <div data-st-price class="st-price"></div>
      <div class="st-sub-label">거래량 <small>(막대 색: 전일 대비 상승 빨강 · 하락 파랑)</small></div>
      <div data-st-volume></div>
      <details class="st-adx" open>
        <summary>추세 강도 ADX14 <small>(점선 20·25는 관행적 눈금 · 방향은 슈퍼트렌드로 따로 확인)</small></summary>
        <label class="st-toggle"><input type="checkbox" data-st-di> +DI / -DI 표시</label>
        <div data-st-adx></div>
      </details>
      <p class="dash-note">차트를 누르면 그 날짜의 값이 아래 상태표에 나옵니다. 키보드: 상태표의 ◀ ▶ 버튼.</p>
    </section>

    <div class="st-grid">
      <section class="dash-card" data-st-selected></section>
      <section class="dash-card" data-st-flips></section>
    </div>

    <section class="dash-card st-calc">
      <h2 class="st-h2">가격 거리 계산 <small>(차트용 · 저장·원장 연결 없음)</small></h2>
      <form class="st-calc-form" data-st-calc>
        <label class="st-field"><span>계획 가격 P</span><input type="number" step="any" min="0" data-calc="p"></label>
        <label class="st-field"><span>무효화 기준 S</span><input type="number" step="any" min="0" data-calc="s"></label>
        <label class="st-field"><span>수량 Q</span><input type="number" step="1" min="0" data-calc="q"></label>
        <label class="st-field"><span>목표 T (선택)</span><input type="number" step="any" min="0" data-calc="t"></label>
      </form>
      <div data-st-calc-out class="st-calc-out"></div>
    </section>

    <section class="dash-card st-help">
      <h2 class="st-h2">읽는 순서</h2>
      <ol>
        <li><b>매수 검토</b>: 확정 상승 전환과 주봉 방향 → ADX·거래량 배율·최근 전환 횟수 → 추세선 거리·직전 고점으로 지금 가격에서 감수할 거리를 판단.</li>
        <li><b>보유 검토</b>: 일봉 상승 지속과 추세선 변화 → 하락 전환 여부. 하락 전환은 주봉 방향과 무관하게 항상 표시됩니다.</li>
        <li><b>매도 검토</b>: 종가 확정 하락 전환을 우선 확인. 장중에 전일 추세선 아래로 내려간 것은 <b>장중 이탈(미확정)</b>으로 따로 표시합니다.</li>
        <li><b>재진입 검토</b>: 다시 확정 상승 전환이 생기면 새 전환으로 기록됩니다. 실패한 직전 전환도 지우지 않습니다.</li>
      </ol>
      <p class="dash-note">계산: TradingView ta.supertrend와 같은 hl2·RMA ATR·밴드 재귀식. 주봉은 일봉 원 OHLC를 거래소 주 단위로 묶어 따로 계산하고, 각 날짜에는 그 주 이전에 끝난 주의 값만 씁니다.
        ADX 20·25, 거리 1·2 ATR, 거래량 1.5배 같은 눈금은 이해를 돕는 참고선이며 매매 규칙이나 승률 근거가 아닙니다. 이 조합의 수익 개선·최적 설정은 검증되지 않았습니다.
        같은 종목이라도 거래소·세션·수정주가·입력 OHLC가 다르면 TradingView와 전환 날짜가 다를 수 있습니다.</p>
    </section>
  </div>`;

  const $ = (selector) => container.querySelector(selector);
  const search = $('[data-st-search]');
  const atrInput = $('[data-st-atr]');
  const factorInput = $('[data-st-factor]');
  const errorEl = $('[data-st-error]');
  // 모바일은 가격·거래량을 먼저 보이고 보조 패널은 접어 둔다.
  if (window.matchMedia?.('(max-width: 640px)').matches) $('.st-adx').removeAttribute('open');
  search.value = state.name;
  atrInput.value = state.atr;
  factorInput.value = state.factor;
  $('[data-st-ma]').checked = state.showMa;
  $('[data-st-levels]').checked = state.showLevels;

  attachStockSearch(search, { onSelect: (item) => { state.ticker = item.ticker; state.name = item.name; load(); } });

  function persist() {
    savePrefs({ ticker: state.ticker, name: state.name, atr: state.atr, factor: state.factor, range: state.range,
      showMa: state.showMa, showLevels: state.showLevels });
  }

  $('[data-st-form]').addEventListener('submit', (event) => {
    event.preventDefault();
    const raw = search.value.trim();
    // 국내 자동완성에서 고르지 않은 입력은 티커로 본다(미국 종목·지수 등).
    if (!search.dataset.ticker && raw && raw !== state.name) {
      if (!/^[A-Za-z0-9.^=-]{1,24}$/.test(raw)) { errorEl.textContent = '목록에서 국내 종목을 고르거나 티커(예: AAPL, 005930.KS)를 입력하세요.'; return; }
      state.ticker = raw.toUpperCase(); state.name = raw.toUpperCase();
    }
    const atr = Number(atrInput.value);
    const factor = Number(factorInput.value);
    if (!Number.isInteger(atr) || atr < 1 || atr > 100) { errorEl.textContent = 'ATR 기간은 1~100 사이 정수입니다.'; return; }
    if (!(factor >= 0.1 && factor <= 20)) { errorEl.textContent = 'ATR 배수는 0.1~20 사이입니다.'; return; }
    state.atr = atr; state.factor = factor;
    load();
  });
  $('[data-st-reset]').addEventListener('click', () => {
    atrInput.value = DEFAULTS.atr; factorInput.value = DEFAULTS.factor;
    state.atr = DEFAULTS.atr; state.factor = DEFAULTS.factor; load();
  });
  container.querySelectorAll('[data-st-range]').forEach((button) => button.addEventListener('click', () => {
    state.range = button.dataset.stRange; persist(); renderCharts();
  }));
  $('[data-st-ma]').addEventListener('change', (event) => { state.showMa = event.target.checked; persist(); renderCharts(); });
  $('[data-st-levels]').addEventListener('change', (event) => { state.showLevels = event.target.checked; persist(); renderCharts(); });
  $('[data-st-di]').addEventListener('change', (event) => { state.showDi = event.target.checked; renderCharts(); });
  $('[data-st-calc]').addEventListener('input', renderCalc);

  async function load() {
    const current = ++requestId;
    errorEl.textContent = '';
    $('[data-st-status]').innerHTML = '<p class="efa-muted">불러오는 중…</p>';
    try {
      const query = new URLSearchParams({ ticker: state.ticker, atr: state.atr, factor: state.factor });
      const response = await fetch(`/api/chart/supertrend?${query}`);
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
      if (!alive || current !== requestId) return;
      state.data = data;
      state.selected = null;
      persist();
    } catch (error) {
      if (!alive || current !== requestId) return;
      state.data = null;
      destroyCharts();
      $('[data-st-status]').innerHTML = `<p class="efa-error">차트 데이터 없음: ${esc(error.message)}</p>`;
      $('[data-st-selected]').innerHTML = ''; $('[data-st-flips]').innerHTML = '';
      return;
    }
    renderAll();
  }

  function bars() { return state.data?.bars || []; }
  function displayStart() { return Math.max(0, bars().length - RANGES[state.range]); }
  function selectedIndex() { return state.selected ?? bars().length - 1; }

  function renderAll() {
    renderStatus();
    renderCharts();
    renderSelected();
    renderFlips();
    renderCalc();
  }

  function renderStatus() {
    const data = state.data;
    const rows = bars();
    const last = rows.at(-1);
    const lastConfirmed = data.last_confirmed ? last : rows.at(-2);
    const combo = COMBO[`${lastConfirmed?.weekly_dir}|${lastConfirmed?.dir}`];
    const quality = data.data || {};
    const bench = data.benchmark?.ticker ? `${esc(data.benchmark.ticker)} ${dirLabel(last.benchmark_dir)}` : `<span class="efa-muted">제공 안 됨</span>`;
    $('[data-st-status]').innerHTML = `
      <div class="st-status-grid">
        <div><small>확정 일봉 추세</small>${dirLabel(lastConfirmed?.dir)}<em>${esc(lastConfirmed?.date || '-')} 종가 기준</em></div>
        <div><small>직전 확정 주봉</small>${dirLabel(lastConfirmed?.weekly_dir)}<em>${lastConfirmed?.weekly_ref_start ? `${esc(lastConfirmed.weekly_ref_start)} ~ ${esc(lastConfirmed.weekly_ref_end)} 주` : '-'}</em></div>
        <div><small>최근 확정 전환</small><b>${lastConfirmed?.last_flip_type ? flipLabel(lastConfirmed.last_flip_type) : '없음'}</b>
          <em>${lastConfirmed?.last_flip_date ? `${esc(lastConfirmed.last_flip_date)} · ${fmt(lastConfirmed.last_flip_close)} · ${lastConfirmed.days_since_flip}거래일째` : '-'}</em></div>
        <div><small>비교 지수(일봉 ST)</small>${bench}<em>${data.benchmark?.error ? '비교 지수 조회 실패' : '같은 설정'}</em></div>
        <div><small>마지막 봉</small><b>${data.last_confirmed ? '확정' : '미확정(장중·수집 전)'}</b>
          <em>${esc(last.date)} · ${fmt(last.c)}${last.intraday_break ? ' · <span class="st-warn">장중 이탈(미확정)</span>' : ''}</em></div>
      </div>
      ${combo ? `<p class="st-combo"><b>${combo[0]}</b> — ${combo[1]} <small>(조건 요약이며 추천·확률이 아닙니다. 주봉은 후행하므로 불일치가 곧 나쁜 신호는 아닙니다.)</small></p>` : ''}
      <p class="dash-note">자료: ${esc(quality.source || '-')} · 수정주가 · ${esc(quality.first_bar_date)} ~ ${esc(quality.last_bar_date)} (${fmt(quality.bar_count, 0)}봉, 전체 이력으로 계산 후 표시 구간만 자름)
        · 데이터셋 ${esc(String(quality.dataset_version || '-').slice(0, 12))}${quality.stale ? ' · <span class="st-warn">최근 봉이 오래됨</span>' : ''}</p>`;
  }

  function destroyCharts() {
    charts.splice(0).forEach((chart) => { try { chart.destroy(); } catch { /* 이미 해제 */ } });
    ['[data-st-price]', '[data-st-volume]', '[data-st-adx]'].forEach((selector) => { $(selector).innerHTML = ''; });
  }

  function renderCharts() {
    try { drawCharts(); } catch (error) {
      // 그리기 오류는 시세 없음과 다르다. 상태표·전환 기록은 그대로 둔다.
      destroyCharts();
      $('[data-st-price]').innerHTML = `<p class="efa-error">차트 그리기 오류: ${esc(error.message)}</p>`;
    }
  }

  function drawCharts() {
    container.querySelectorAll('[data-st-range]').forEach((button) => button.classList.toggle('is-active', button.dataset.stRange === state.range));
    if (!state.data || typeof ApexCharts === 'undefined') return;
    destroyCharts();
    const all = bars();
    const start = displayStart();
    const shown = all.slice(start);
    const x = (row) => new Date(`${row.date}T00:00:00`).getTime();
    const confirmedSt = (row, direction) => (row.confirmed && row.dir === direction && num(row.st) ? row.st : null);
    const series = [
      { name: state.name, type: 'candlestick', data: shown.map((row) => ({ x: x(row), y: [row.o, row.h, row.l, row.c] })) },
      { name: 'ST 상승', type: 'line', data: shown.map((row) => ({ x: x(row), y: confirmedSt(row, -1) })) },
      { name: 'ST 하락', type: 'line', data: shown.map((row) => ({ x: x(row), y: confirmedSt(row, 1) })) },
    ];
    const colors = [COLORS.candleUp, COLORS.up, COLORS.down];
    const widths = [1, 2.2, 2.2];
    const dashes = [0, 0, 0];
    const last = all.at(-1);
    if (!state.data.last_confirmed && num(last.st) && shown.length > 1) {
      // 미확정 봉의 추세선은 점선으로만 잇고 전환으로 세지 않는다.
      const prev = shown.at(-2);
      series.push({ name: 'ST 미확정', type: 'line', data: shown.map((row, i) => ({ x: x(row), y: i >= shown.length - 2 ? (i === shown.length - 1 ? last.st : prev.st) : null })) });
      colors.push(COLORS.muted); widths.push(2); dashes.push(5);
    }
    if (state.showMa) {
      [['SMA20', 'sma20', '#facc15'], ['SMA60', 'sma60', '#a855f7'], ['SMA200', 'sma200', '#94a3b8']].forEach(([name, key, color]) => {
        series.push({ name, type: 'line', data: shown.map((row) => ({ x: x(row), y: row[key] })) });
        colors.push(color); widths.push(1.2); dashes.push(0);
      });
    }
    if (state.showLevels) {
      [['직전20일 고점', 'high20', '#f97316'], ['직전20일 저점', 'low20', '#38bdf8']].forEach(([name, key, color]) => {
        series.push({ name, type: 'line', data: shown.map((row) => ({ x: x(row), y: row[key] })) });
        colors.push(color); widths.push(1); dashes.push(3);
      });
    }
    const narrow = window.matchMedia?.('(max-width: 640px)').matches;
    // 3년 보기와 휴대폰 폭에서는 글자 라벨이 서로·가격축과 겹치므로 점만 찍는다.
    const compact = state.range === '3y' || narrow;
    const points = (state.data.flips || []).filter((flip) => flip.date >= shown[0].date).map((flip) => {
      const row = all.find((item) => item.date === flip.date);
      const up = flip.type === 'up';
      const color = up ? COLORS.up : COLORS.down;
      // 주봉 방향과 같은 전환은 채운 라벨, 반대거나 주봉 계산 전이면 테두리만 있는 라벨.
      const filled = flip.weekly_aligned === true;
      return {
        x: x(row), y: up ? row.l : row.h,
        marker: { size: 4, fillColor: color, strokeColor: '#0a0e17', strokeWidth: 1 },
        label: compact ? { text: '' , borderWidth: 0, style: { background: 'transparent' } } : {
          text: up ? 'BUY·상승' : 'SELL·하락', borderColor: color, borderWidth: 1, offsetY: up ? 34 : -6,
          style: { background: filled ? color : '#0a0e17', color: filled ? '#0a0e17' : color, fontSize: '10px', fontWeight: 700, padding: { left: 4, right: 4, top: 1, bottom: 1 } },
        },
      };
    });
    const sel = all[selectedIndex()];
    const selectionLine = sel && sel.date >= shown[0].date ? [{ x: x(sel), borderColor: COLORS.select, strokeDashArray: 3, opacity: 0.8 }] : [];
    const lows = shown.map((row) => row.l);
    const highs = shown.map((row) => row.h);
    const common = {
      chart: { group: 'st-sync', background: 'transparent', foreColor: '#8a97ad', animations: { enabled: false },
        toolbar: { show: false }, zoom: { enabled: false },
        events: {
          mouseMove: (_event, _ctx, config) => { if (config.dataPointIndex >= 0) state.hovered = start + config.dataPointIndex; },
          click: (_event, _ctx, config) => {
            const index = config.dataPointIndex >= 0 ? start + config.dataPointIndex : state.hovered;
            // 이벤트 처리 중인 차트를 바로 파괴하지 않도록 다음 틱에 다시 그린다.
            if (index !== null && index !== undefined) window.setTimeout(() => { if (!alive) return; state.selected = index; renderSelected(); renderCharts(); }, 0);
          },
        } },
      grid: { borderColor: '#1e2a3f', strokeDashArray: 3 },
      xaxis: { type: 'datetime', labels: { datetimeUTC: false, format: 'yy-MM-dd', style: { fontSize: '10px' } }, tooltip: { enabled: false } },
      tooltip: { shared: true, intersect: false, x: { format: 'yyyy-MM-dd' } },
      dataLabels: { enabled: false }, legend: { show: false },
      annotations: { xaxis: selectionLine },
    };
    const yLabels = { minWidth: 56, maxWidth: 56 };
    const price = new ApexCharts($('[data-st-price]'), { ...common,
      chart: { ...common.chart, id: 'st-price', type: 'candlestick', height: narrow ? 300 : 420,
        toolbar: { show: true, tools: { download: false, selection: false, zoom: true, zoomin: true, zoomout: true, pan: true, reset: true } },
        zoom: { enabled: true, type: 'x' } },
      series, colors,
      // 캔들 테두리 외의 선 색을 명시하지 않으면 ApexCharts가 회색으로 그린다.
      stroke: { width: widths, dashArray: dashes, curve: 'straight', colors: [COLORS.muted, ...colors.slice(1)] },
      plotOptions: { candlestick: { colors: { upward: COLORS.candleUp, downward: COLORS.candleDown }, wick: { useFillColor: true } } },
      xaxis: { ...common.xaxis, labels: { ...common.xaxis.labels, show: false } },
      yaxis: { opposite: true, min: Math.min(...lows) * 0.97, max: Math.max(...highs) * 1.03, tickAmount: 6,
        labels: { ...yLabels, formatter: (value) => fmt(value, value >= 1000 ? 0 : 2) } },
      annotations: { xaxis: selectionLine, points },
      tooltip: { shared: true, intersect: false, x: { format: 'yyyy-MM-dd' }, y: { formatter: (value) => (num(value) ? fmt(value) : '') } },
    });
    const hasVolume = shown.some((row) => num(row.v) && row.v > 0);
    const volume = new ApexCharts($('[data-st-volume]'), { ...common,
      chart: { ...common.chart, id: 'st-volume', type: 'bar', height: narrow ? 90 : 110 },
      series: [{ name: '거래량', data: shown.map((row, i) => {
        const prevClose = i ? shown[i - 1].c : (start ? all[start - 1].c : row.o);
        return { x: x(row), y: num(row.v) ? row.v : null, fillColor: row.c >= prevClose ? COLORS.candleUp : COLORS.candleDown };
      }) }],
      plotOptions: { bar: { columnWidth: '75%' } },
      xaxis: { ...common.xaxis, labels: { ...common.xaxis.labels, show: false } },
      yaxis: { opposite: true, labels: { ...yLabels, formatter: (value) => (value >= 1e6 ? `${(value / 1e6).toFixed(1)}M` : fmt(value, 0)) } },
      noData: { text: '거래량 제공 안 됨' },
    });
    const adxSeries = [{ name: 'ADX14', data: shown.map((row) => ({ x: x(row), y: row.adx })) }];
    const adxColors = ['#f0b90b'];
    if (state.showDi) {
      adxSeries.push({ name: '+DI', data: shown.map((row) => ({ x: x(row), y: row.plus_di })) }, { name: '-DI', data: shown.map((row) => ({ x: x(row), y: row.minus_di })) });
      adxColors.push(COLORS.up, COLORS.down);
    }
    const adx = new ApexCharts($('[data-st-adx]'), { ...common,
      chart: { ...common.chart, id: 'st-adx', type: 'line', height: narrow ? 120 : 150 },
      series: adxSeries, colors: adxColors, stroke: { width: 1.6, curve: 'straight', colors: adxColors },
      yaxis: { opposite: true, min: 0, max: Math.max(50, ...shown.map((row) => row.adx || 0), ...(state.showDi ? shown.map((row) => Math.max(row.plus_di || 0, row.minus_di || 0)) : [])),
        tickAmount: 4, labels: { ...yLabels, formatter: (value) => fmt(value, 0) } },
      annotations: { xaxis: selectionLine, yaxis: [20, 25].map((y) => ({ y, borderColor: '#565f74', strokeDashArray: 4 })) },
    });
    charts = hasVolume ? [price, volume, adx] : [price, adx];
    if (!hasVolume) $('[data-st-volume]').innerHTML = '<p class="dash-note">거래량 제공 안 됨</p>';
    charts.forEach((chart) => chart.render());
    $('[data-st-title]').textContent = `${state.name} (${state.data.ticker}) · 일봉 · ATR ${state.data.settings.atr} × ${state.data.settings.factor}`;
    $('[data-st-legend]').innerHTML = `<span style="color:${COLORS.up}">━ 상승 ST</span> · <span style="color:${COLORS.down}">━ 하락 ST</span>${state.data.last_confirmed ? '' : ' · ┅ 미확정'}
      · 라벨: 채움 = 직전 확정 주봉과 같은 방향 / 테두리만 = 반대·계산 전${compact ? ' · 3년 보기·휴대폰에서는 점만 표시(상태표·전환 기록에서 확인)' : ''} · 캔들 상승 빨강·하락 파랑`;
  }

  function metric(label, value, hint = '') {
    return `<tr><th>${label}</th><td>${value}</td><td class="efa-muted">${hint}</td></tr>`;
  }

  function renderSelected() {
    const all = bars();
    const index = selectedIndex();
    const row = all[index];
    if (!row) return;
    const combo = COMBO[`${row.weekly_dir}|${row.dir}`];
    const lineLabel = row.dir === -1 ? '상승 추세선' : row.dir === 1 ? '하락 추세선' : '추세선';
    const prevLine = row.prev_dir !== null && row.prev_dir !== row.dir
      ? `전일 확정 ${row.prev_dir === -1 ? '상승' : '하락'}선 ${fmt(row.prev_st)} (전환일: 새 선은 반대쪽으로 이동)` : '';
    $('[data-st-selected]').innerHTML = `
      <div class="st-sel-head">
        <h2 class="st-h2">${esc(row.date)} 상태 ${row.confirmed ? '' : '<span class="st-warn">미확정</span>'}</h2>
        <div class="st-sel-nav">
          <button type="button" class="dash-btn" data-st-step="-1" aria-label="이전 거래일">◀</button>
          <button type="button" class="dash-btn" data-st-step="1" aria-label="다음 거래일">▶</button>
          <button type="button" class="dash-btn" data-st-latest>최신</button>
        </div>
      </div>
      <div class="efa-table-wrap"><table class="efa-table st-metrics"><tbody>
        ${metric('일봉 / 직전 확정 주봉', `${dirLabel(row.dir)} / ${dirLabel(row.weekly_dir)}`, combo ? esc(combo[0]) : '')}
        ${metric('오늘 전환', row.flip ? `<b>${flipLabel(row.flip)}</b>` : '없음', row.flip ? '종가 확정 후 신호 · 같은 종가 체결을 가정하지 않음' : '')}
        ${metric('종가 / 시가 갭', `${fmt(row.c)} / ${signed(row.gap_pct)}`, num(row.gap_atr) ? `갭 ${fmt(row.gap_atr)} ATR(전일 ATR 기준)` : '')}
        ${metric(lineLabel, fmt(row.st), esc(prevLine))}
        ${metric('추세선까지 거리', num(row.distance_pct) ? `${fmt(row.distance_pct)}% · ${fmt(row.atr_distance)} ATR` : '-', '거리가 크다고 고평가·하락 확정은 아님')}
        ${metric('ATR / ATR%', `${fmt(row.atr)} / ${fmt(row.atr_pct)}%`, '변동성 수준 · 방향 아님')}
        ${metric('ADX14 (+DI/-DI)', `${fmt(row.adx, 1)} (${fmt(row.plus_di, 1)} / ${fmt(row.minus_di, 1)})`, num(row.adx) ? (row.adx < 20 ? '약한 추세 구간(관행적 눈금)' : row.adx <= 25 ? '중간(관행적 눈금)' : '강한 추세 구간(관행적 눈금)') : '')}
        ${metric('거래량 배율', num(row.volume_ratio) ? `${fmt(row.volume_ratio)}배` : row.confirmed ? '제공 안 됨' : '장중 누적이라 비교 안 함', '직전 20거래일 평균 대비(오늘 제외)')}
        ${metric('최근 20거래일 전환', num(row.flip_count) ? `${row.flip_count}회` : '-', '잦으면 횡보 가능성 · 원 신호는 지우지 않음')}
        ${metric('직전 20일 고점 / 저점', `${fmt(row.high20)} / ${fmt(row.low20)}`, num(row.high20) && row.c > row.high20 ? '<b>직전 고점 돌파</b>' : '오늘 제외 · 목표가 아님')}
        ${metric('직전 60일 고점 / 저점', `${fmt(row.high60)} / ${fmt(row.low60)}`, num(row.high60) && row.c > row.high60 ? '<b>직전 60일 고점 돌파</b>' : '')}
        ${metric('최근 전환 이후', row.last_flip_date ? `${flipLabel(row.last_flip_type)} ${esc(row.last_flip_date)} · ${row.days_since_flip}거래일 · ${signed(row.change_since_flip_pct)}` : '-', '신호 후 가격 변화(매매 수익률 아님)')}
        ${metric('비교 지수 일봉 ST', dirLabel(row.benchmark_dir), esc(state.data.benchmark?.ticker || ''))}
      </tbody></table></div>`;
    const go = (next) => { state.selected = Math.max(0, Math.min(all.length - 1, next)); renderSelected(); renderCharts(); };
    container.querySelectorAll('[data-st-step]').forEach((button) => button.addEventListener('click', () => go(index + Number(button.dataset.stStep))));
    $('[data-st-latest]').addEventListener('click', () => { state.selected = null; renderSelected(); renderCharts(); });
  }

  function renderFlips() {
    const all = bars();
    const flips = state.data.flips || [];
    const indexOf = new Map(all.map((row, i) => [row.date, i]));
    const items = flips.map((flip, k) => {
      const at = indexOf.get(flip.date);
      const end = k + 1 < flips.length ? indexOf.get(flips[k + 1].date) : all.length - 1;
      return { ...flip, at, held: end - at, until: k + 1 < flips.length ? flips[k + 1].date : null };
    }).reverse().slice(0, 30);
    $('[data-st-flips]').innerHTML = `<h2 class="st-h2">확정 전환 기록 <small>(최근 30개 · 실패한 전환도 그대로)</small></h2>
      ${items.length ? `<div class="efa-table-wrap"><table class="efa-table st-flip-table">
        <thead><tr><th>날짜</th><th>전환</th><th class="is-num">확정 종가</th><th>직전 확정 주봉</th><th class="is-num">유지</th></tr></thead>
        <tbody>${items.map((item) => `<tr data-st-goto="${item.at}" tabindex="0">
          <td>${esc(item.date)}</td>
          <td class="${item.type === 'up' ? 'st-up' : 'st-down'}">${flipLabel(item.type)}</td>
          <td class="is-num">${fmt(item.close)}</td>
          <td>${dirLabel(item.weekly_dir)} ${item.weekly_aligned === false ? '<small class="efa-muted">반대</small>' : ''}</td>
          <td class="is-num">${item.until ? `${item.held}거래일` : `${item.held}거래일째`}</td>
        </tr>`).join('')}</tbody></table></div>` : '<p class="efa-muted">이력 안에 확정 전환이 없습니다.</p>'}
      <p class="dash-note">유지 = 다음 반대 전환까지 거래일. 사후 결과로 과거 신호를 지우거나 바꾸지 않습니다.</p>`;
    container.querySelectorAll('[data-st-goto]').forEach((tr) => {
      const go = () => { state.selected = Number(tr.dataset.stGoto); renderSelected(); renderCharts(); };
      tr.addEventListener('click', go);
      tr.addEventListener('keydown', (event) => { if (event.key === 'Enter') go(); });
    });
  }

  function renderCalc() {
    const out = $('[data-st-calc-out]');
    const value = (key) => { const raw = container.querySelector(`[data-calc="${key}"]`).value; return raw === '' ? null : Number(raw); };
    const [p, s, q, t] = ['p', 's', 'q', 't'].map(value);
    const lines = [];
    if (num(p) && num(s) && p > 0 && s > 0) {
      lines.push(`P−S 가격 차이: <b>${fmt(p - s)}</b> (${signed(((s / p) - 1) * 100)})`);
      if (num(q) && q > 0) lines.push(`(P−S)×Q 금액 차이: <b>${fmt((p - s) * q, 0)}</b>`);
      if (num(t)) {
        lines.push(s < p && p < t ? `R배수 (T−P)/(P−S): <b>${fmt((t - p) / (p - s))}</b>` : 'R배수는 S &lt; P &lt; T일 때만 계산합니다.');
      }
    }
    const last = bars().filter((row) => row.confirmed).at(-1);
    if (num(p) && last?.dir === -1 && num(last.st)) {
      lines.push(`최근 확정 상승 추세선 ${fmt(last.st)}은 P보다 ${last.st >= p ? '<b>위</b>' : '아래'}에 있습니다. (이익 보장 아님)`);
    }
    out.innerHTML = lines.length
      ? `<ul>${lines.map((line) => `<li>${line}</li>`).join('')}</ul><p class="dash-note">수수료·세금·갭·슬리피지 미반영 단순 계산. 직전 고점을 목표가로 자동 지정하지 않습니다. 입력값은 저장되지 않습니다.</p>`
      : '<p class="dash-note">P와 S를 입력하면 가격 차이를, Q·T를 더하면 금액 차이와 R배수를 보여 줍니다.</p>';
  }

  load();
  window._viewCleanup = () => { alive = false; destroyCharts(); };
}
