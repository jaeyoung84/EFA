import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';
import { TIME_FORMAT } from '../utils/liveQuotes.js';

const emptyHolding = () => ({ ticker: '', name: '', quantity: '', average_price: '', currency: 'KRW', sector: '미분류' });
const money = (value) => new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(Number(value) || 0);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));

const domesticStockCode = (ticker) => String(ticker || '').trim().toUpperCase().match(/^(\d{6})(?:\.(?:KS|KQ))?$/)?.[1] || null;
const needsSectorClassification = (sector) => !String(sector || '').trim() || String(sector).trim() === '미분류';

async function classifySectors(root) {
  const rows = root.matches?.('[data-holding-row]') ? [root] : [...root.querySelectorAll('[data-holding-row]')];
  const targets = rows.map((row) => ({
    row,
    code: domesticStockCode(row.querySelector('[data-field="ticker"]').value),
    sectorInput: row.querySelector('[data-field="sector"]'),
  })).filter((item) => item.code && needsSectorClassification(item.sectorInput.value));
  const codes = [...new Set(targets.map((item) => item.code))];
  if (!codes.length) return 0;

  try {
    const batches = [];
    for (let index = 0; index < codes.length; index += 30) {
      batches.push(authRequest(`/api/market/stock-sectors?symbols=${encodeURIComponent(codes.slice(index, index + 30).join(','))}`));
    }
    const responses = await Promise.all(batches);
    const sectors = new Map(responses.flatMap((response) => response.items || []).filter((item) => item.sector_name).map((item) => [item.symbol, item.sector_name]));
    let changed = 0;
    targets.forEach(({ row, code, sectorInput }) => {
      // 조회 중 사용자가 다른 종목이나 업종을 입력했다면 그 값을 덮어쓰지 않는다.
      if (domesticStockCode(row.querySelector('[data-field="ticker"]').value) !== code || !needsSectorClassification(sectorInput.value)) return;
      const sector = sectors.get(code);
      if (!sector) return;
      sectorInput.value = sector;
      sectorInput.title = 'DART 기업개황의 한국표준산업분류 기준으로 자동 입력됨';
      changed += 1;
    });
    return changed;
  } catch {
    // DART 키·네트워크가 일시적으로 없더라도 포트폴리오 조회와 저장은 계속 가능해야 한다.
    return 0;
  }
}

export async function portfolioValuation(portfolio) {
  const holdings = portfolio.holdings || [];
  if (!holdings.length) return { available: true, value_krw: Number(portfolio.cash_krw) || 0, profit_krw: 0, return_pct: 0, priced_count: 0, holding_count: 0, positions: [] };
  return authRequest('/api/portfolio/valuation');
}

/** 일부 종목 시세가 없을 때: 총평가는 '평가 불가'로 두되, 비중·집중도는 시세가 확인된 종목(+현금) 기준으로 계산한다. */
const VALUATION_SOURCE = {
  kis_live: '한투 체결가(장중 실시간)',
  mixed_with_kis: '한투 체결가 + 지연 시세·검증 캐시',
  market_data_cache_daily: '검증 캐시 · 일봉 종가',
  mixed_delayed_quotes: '지연 시세·검증 캐시 혼합',
};

export function valuationSourceText(valuation) {
  const source = VALUATION_SOURCE[valuation.source] || VALUATION_SOURCE.mixed_delayed_quotes;
  const at = (valuation.positions || []).map((item) => item.quoted_at).filter(Boolean).sort().at(-1);
  if (!at) return source;
  const live = valuation.source === 'kis_live' || valuation.source === 'mixed_with_kis';
  const stamp = live ? `${new Date(at).toLocaleTimeString('ko-KR', TIME_FORMAT)} 시세` : `기준일 ${at.slice(0, 10)}`;
  return `${source} · ${stamp}${valuation.live_refresh_seconds ? ` · ${valuation.live_refresh_seconds}초마다 갱신` : ''}`;
}

export function weightBasis(portfolio, valuation) {
  if (valuation.available) return { valuation, partial: false, priced: valuation.priced_count, excluded: 0 };
  const priced = (valuation.positions || []).filter((item) => item.status === 'ok' && Number.isFinite(item.value_krw));
  const value = (Number(portfolio.cash_krw) || 0) + priced.reduce((sum, item) => sum + item.value_krw, 0);
  return { valuation: { ...valuation, value_krw: value }, partial: true, priced: priced.length,
           excluded: (valuation.positions || []).length - priced.length };
}

export function portfolioInsights(portfolio, valuation) {
  const total = Number(valuation.value_krw) || 0;
  const positions = [
    ...(Number(portfolio.cash_krw) > 0 ? [{ ticker: 'CASH', name: '현금', currency: 'KRW', value_krw: Number(portfolio.cash_krw), profit_krw: 0 }] : []),
    ...(valuation.positions || []),
  ].map((item) => ({ ...item, weight_pct: total && Number.isFinite(item.value_krw) ? item.value_krw / total * 100 : NaN }));
  // 시세가 없는 종목(value_krw 없음)은 정렬·집중도 계산에서 빼고 목록 끝에 둔다.
  const priced = positions.filter((item) => Number.isFinite(item.value_krw)).sort((a, b) => b.value_krw - a.value_krw);
  const unpriced = positions.filter((item) => !Number.isFinite(item.value_krw));
  const currency = priced.reduce((result, item) => { result[item.currency] = (result[item.currency] || 0) + item.value_krw; return result; }, {});
  const maxPosition = priced[0] || null;
  const hhi = priced.reduce((sum, item) => sum + (item.weight_pct / 100) ** 2, 0);
  return { total, positions: [...priced, ...unpriced], currency, max_position: maxPosition, hhi, concentration: hhi >= 0.45 ? '높음' : hhi >= 0.25 ? '보통' : '낮음' };
}

function holdingRow(holding, index) {
  return `<tr data-holding-row="${index}">
    <td><div class="holdings-ticker-search" data-selected-name="${escapeHtml(holding.name)}">
      <input data-field="name" value="${escapeHtml(holding.name)}" placeholder="예: 삼성전자, Apple" maxlength="80" autocomplete="off" aria-label="종목명 검색">
      <input data-field="ticker" type="hidden" value="${escapeHtml(holding.ticker)}">
      <small class="holdings-selected-ticker" ${holding.ticker ? '' : 'hidden'}>${holding.ticker ? `자동 연결: ${escapeHtml(holding.ticker)}` : ''}</small>
      <ul class="holdings-search-results" hidden></ul>
    </div></td>
    <td><input data-field="quantity" type="number" min="0.000001" step="any" value="${escapeHtml(holding.quantity)}" aria-label="수량"></td>
    <td><input data-field="average_price" type="number" min="0" step="any" value="${escapeHtml(holding.average_price)}" aria-label="평균 단가"></td>
    <td><select data-field="currency" aria-label="통화"><option value="KRW" ${holding.currency === 'KRW' ? 'selected' : ''}>KRW</option><option value="USD" ${holding.currency === 'USD' ? 'selected' : ''}>USD</option></select></td>
    <td><input data-field="sector" value="${escapeHtml(holding.sector || '미분류')}" placeholder="국내 종목 자동 분류" maxlength="40" aria-label="업종"></td>
    <td><button type="button" class="holdings-remove" aria-label="종목 삭제"><i class="fa-solid fa-trash-can"></i></button></td>
  </tr>`;
}

function readHoldings(container) {
  return [...container.querySelectorAll('[data-holding-row]')].map((row) => ({
    ticker: row.querySelector('[data-field="ticker"]').value.trim(),
    name: row.querySelector('[data-field="name"]').value.trim(),
    quantity: Number(row.querySelector('[data-field="quantity"]').value),
    average_price: Number(row.querySelector('[data-field="average_price"]').value),
    currency: row.querySelector('[data-field="currency"]').value,
    sector: row.querySelector('[data-field="sector"]').value.trim() || '미분류',
  })).filter((holding) => holding.ticker || holding.name || holding.quantity || holding.average_price);
}

export const MAX_LISTED_HOLDINGS = 20;

/** "보유 N개 중 상위 20개 표시 (최대 20개)" / "보유 N개 전체 표시 (최대 20개)" */
export function listedNote(shown, total, unit = '종목') {
  return total > shown
    ? `${unit} ${total}개 중 상위 ${shown}개 표시 (최대 ${MAX_LISTED_HOLDINGS}개)`
    : `${unit} ${total}개 전체 표시 (최대 ${MAX_LISTED_HOLDINGS}개)`;
}

export function weightListHtml(positions) {
  const priced = positions.filter((item) => Number.isFinite(item.weight_pct) && item.value_krw > 0);
  const shown = priced.slice(0, MAX_LISTED_HOLDINGS);
  const missing = positions.length - priced.length;
  return `<p class="holdings-list-note">${listedNote(shown.length, priced.length, '자산(현금 포함)')}${missing ? ` · 시세 없음 ${missing}개 제외` : ''}</p>
    <ol class="holdings-weight-list">${shown.map((item) => `<li><span>${escapeHtml(item.name || item.ticker)} <small>${escapeHtml(item.ticker)}</small></span><b>${item.weight_pct.toFixed(1)}%</b><i><em style="width:${Math.min(item.weight_pct, 100)}%"></em></i></li>`).join('')}</ol>`;
}

export function factorExposureHtml(data, names = {}) {
  if (!data.available) return `<header><div><span class="jy-section-label">FACTOR EXPOSURE</span><h2>보유 팩터 노출</h2></div></header><p class="holdings-valuation-note">${escapeHtml(data.reason || '팩터 데이터를 준비 중입니다.')}</p>`;
  const factors = data.weighted_factors || {};
  const pct = (value) => value == null ? '-' : `${(Number(value) * 100).toFixed(2)}%`;
  const positions = data.positions || [];
  const covered = new Set(positions.map((position) => position.ticker));
  const missing = (data.excluded || []).filter((item) => !covered.has(item.ticker));
  const total = positions.length + missing.length;
  const rows = [
    ...positions.map((position) => `<tr><td>${escapeHtml(position.name || position.ticker)}</td><td>${position.weight_pct.toFixed(1)}%</td><td>${pct(position.factors.return_20d)}</td><td>${pct(position.factors.realized_volatility_20d)}</td><td>${pct(position.factors.max_drawdown_60d)}</td></tr>`),
    ...missing.map((item) => `<tr class="is-missing"><td>${escapeHtml(names[item.ticker] || item.ticker)}</td><td colspan="4" class="holdings-missing-reason">팩터 데이터 없음 · ${escapeHtml(item.reason || '')}</td></tr>`),
  ].slice(0, MAX_LISTED_HOLDINGS).join('');
  return `<header><div><span class="jy-section-label">FACTOR EXPOSURE</span><h2>보유 팩터 노출</h2></div><small>${escapeHtml(data.as_of)} 기준 · 종목 커버리지 ${data.coverage_pct}%</small></header><div class="holdings-insight-grid"><article><span>20일 수익률</span><strong>${pct(factors.return_20d)}</strong><small>보유비중 가중 관측값</small></article><article><span>20일 변동성</span><strong>${pct(factors.realized_volatility_20d)}</strong><small>연율화 실현변동성</small></article><article><span>60일 최대낙폭</span><strong>${pct(factors.max_drawdown_60d)}</strong><small>보유비중 가중 관측값</small></article></div><p class="holdings-list-note">${listedNote(Math.min(total, MAX_LISTED_HOLDINGS), total)}${missing.length ? ` · 팩터 데이터 없는 ${missing.length}개는 사유 표시(비중 계산 제외)` : ''}</p><div class="holdings-table-wrap"><table><thead><tr><th>종목</th><th>비중</th><th>20일 수익률</th><th>20일 변동성</th><th>60일 MDD</th></tr></thead><tbody>${rows}</tbody></table></div><p class="holdings-valuation-note">${escapeHtml(data.disclaimer)}</p>`;
}

export function portfolioHoldingsView(container) {
  if (!isAuthenticated()) {
    container.innerHTML = `<section class="holdings-login"><span class="jy-section-label">MY PORTFOLIO</span><h1>내 포트폴리오를 연결하세요</h1><p>보유 종목, 수량, 평균 단가와 현금은 로그인한 계정에만 저장됩니다.</p><button type="button" class="run-btn" id="holdings-login"><i class="fa-solid fa-right-to-bracket"></i> 로그인하고 시작하기</button></section>`;
    container.querySelector('#holdings-login').addEventListener('click', openAuthModal);
    return;
  }

  container.innerHTML = `<section class="holdings-view">
    <header class="holdings-head"><div><span class="jy-section-label">MY PORTFOLIO</span><h1>보유 자산</h1><p>장부 기준 보유 내역입니다. 시세 평가와 주문 연동은 다음 단계에서 추가됩니다.</p></div><div><strong id="holdings-book-value">₩0</strong><small>장부 기준 자산</small></div></header>
    <div class="holdings-summary"><article><span>현금</span><strong id="holdings-cash-summary">₩0</strong></article><article><span>보유 종목</span><strong id="holdings-count">0개</strong></article><article><span>취득금액</span><strong id="holdings-cost">₩0</strong></article><article><span>평가손익</span><strong id="holdings-profit">조회 전</strong></article></div><p id="holdings-valuation-note" class="holdings-valuation-note">가격 데이터를 불러오는 중…</p>
    <section class="holdings-insights" id="holdings-insights" hidden></section>
    <section class="holdings-insights" id="holdings-factor-exposure" hidden></section>
    <section class="holdings-editor"><header><h2>보유 내역 입력</h2><p>종목명만 입력하세요. 정확히 일치하는 종목은 자동으로 연결되며, 이름이 비슷한 종목이 여러 개면 검색 목록에서 선택할 수 있습니다. 국내 종목 업종은 DART 기업개황 기준으로 자동 분류되며, 해외 종목이나 분류 자료가 없는 종목은 직접 입력할 수 있습니다.</p></header><label class="holdings-cash-label">보유 현금 (KRW)<input id="holdings-cash" type="number" min="0" step="1000" value="0"></label><div class="holdings-table-wrap"><table><thead><tr><th>종목명</th><th>수량</th><th>평균 단가</th><th>통화</th><th>업종</th><th></th></tr></thead><tbody id="holdings-rows"></tbody></table></div><div class="holdings-actions"><button type="button" id="holdings-add"><i class="fa-solid fa-plus"></i> 종목 추가</button><button type="button" class="run-btn" id="holdings-save"><i class="fa-solid fa-floppy-disk"></i> 포트폴리오 저장</button></div><p id="holdings-status" class="holdings-status" aria-live="polite"></p></section>
  </section>`;

  const rows = container.querySelector('#holdings-rows');
  const status = container.querySelector('#holdings-status');
  const append = (holding = emptyHolding()) => { rows.insertAdjacentHTML('beforeend', holdingRow(holding, rows.children.length)); };
  const renderSummary = (data) => {
    container.querySelector('#holdings-book-value').textContent = `₩${money(data.book_value_krw)}`;
    container.querySelector('#holdings-cash-summary').textContent = `₩${money(data.cash_krw)}`;
    container.querySelector('#holdings-count').textContent = `${data.holding_count || 0}개`;
    container.querySelector('#holdings-cost').textContent = `₩${money(data.purchase_value_krw)}`;
  };
  const renderValuation = (valuation) => {
    const profit = container.querySelector('#holdings-profit');
    const note = container.querySelector('#holdings-valuation-note');
    if (!valuation.available) {
      container.querySelector('#holdings-book-value').textContent = '평가 불가';
      profit.textContent = '시세 확인 필요';
      profit.className = '';
      const reasons = (valuation.excluded || []).slice(0, 2).map((item) => `${item.name || item.ticker}: ${item.reason}`).join(' · ');
      note.textContent = `일부 시세를 확인하지 못했습니다. ${valuation.priced_count}/${valuation.holding_count}개 종목만 평가되었습니다.${reasons ? ` ${reasons}` : ''}`;
      return;
    }
    container.querySelector('#holdings-book-value').textContent = `₩${money(valuation.value_krw)}`;
    profit.textContent = `${valuation.profit_krw >= 0 ? '+' : '-'}₩${money(Math.abs(valuation.profit_krw))} (${valuation.return_pct >= 0 ? '+' : ''}${valuation.return_pct.toFixed(2)}%)`;
    profit.className = valuation.profit_krw >= 0 ? 'is-profit' : 'is-loss';
    note.textContent = `${valuationSourceText(valuation)} · ${valuation.priced_count}개 종목 평가 완료 · USD 자산은 USD/KRW 환율로 원화 환산`;
  };
  // 한투 체결가로 평가 중이고 장중이면 서버가 알려 준 주기(3초)로 평가만 다시 한다. 화면을 떠나면 멈춘다.
  let valuationTimer = null;
  const refreshValuation = async (data) => {
    clearTimeout(valuationTimer);
    if (!container.isConnected) return;
    let wait = null;
    if (document.hidden) wait = 5;
    else {
      try { const valuation = await portfolioValuation(data); renderValuation(valuation); renderInsights(data, valuation); wait = valuation.live_refresh_seconds; }
      catch { container.querySelector('#holdings-valuation-note').textContent = '시세를 불러오지 못해 장부 기준 금액을 표시합니다.'; }
    }
    if (wait && container.isConnected) valuationTimer = setTimeout(() => refreshValuation(data), wait * 1000);
  };
  const renderInsights = (data, valuation) => {
    const basis = weightBasis(data, valuation);
    const insight = portfolioInsights(data, basis.valuation);
    const root = container.querySelector('#holdings-insights');
    if ((basis.partial && !basis.priced) || (!valuation.positions?.length && !Number(data.cash_krw))) { root.hidden = true; return; }
    const currencies = Object.entries(insight.currency).map(([currency, value]) => `<div><span>${currency}</span><strong>${(value / insight.total * 100).toFixed(1)}%</strong></div>`).join('');
    root.innerHTML = `<header><div><span class="jy-section-label">PORTFOLIO INSIGHTS</span><h2>비중과 집중도</h2>${basis.partial ? `<small class="holdings-partial">시세 확인된 ${basis.priced}개 종목(+현금) 기준 · ${basis.excluded}개 제외</small>` : ''}</div><span class="holdings-concentration is-${insight.concentration === '높음' ? 'high' : insight.concentration === '보통' ? 'mid' : 'low'}">집중도 ${insight.concentration}</span></header><div class="holdings-insight-grid"><article><span>최대 비중</span><strong>${insight.max_position ? `${escapeHtml(insight.max_position.name || insight.max_position.ticker)} ${insight.max_position.weight_pct.toFixed(1)}%` : '-'}</strong><small>현금도 하나의 자산으로 포함합니다.</small></article><article><span>통화 노출</span><div class="holdings-currency">${currencies}</div><small>USD는 현재 환율 기준 원화 환산입니다.</small></article></div>${weightListHtml(insight.positions)}`;
    root.hidden = false;
  };
  const loadFactorExposure = async () => {
    const root = container.querySelector('#holdings-factor-exposure');
    const names = Object.fromEntries(readHoldings(container).map((holding) => [holding.ticker, holding.name]));
    try { const data = await authRequest('/api/portfolio/factor-exposure'); root.hidden = false; root.innerHTML = factorExposureHtml(data, names); } catch { root.hidden = false; root.innerHTML = factorExposureHtml({ available: false, reason: '팩터 노출 데이터를 불러오지 못했습니다.' }); }
  };
  const load = async () => {
    try {
      let data = await authRequest('/api/portfolio');
      container.querySelector('#holdings-cash').value = data.cash_krw || 0;
      (data.holdings?.length ? data.holdings : [emptyHolding()]).forEach(append);
      renderSummary(data);
      const classifiedCount = await classifySectors(rows);
      if (classifiedCount) {
        data = await authRequest('/api/portfolio', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cash_krw: Number(data.cash_krw) || 0, holdings: readHoldings(container) }) });
        renderSummary(data);
        status.textContent = `${classifiedCount}개 종목의 업종을 자동 분류해 저장했습니다.`;
        status.classList.add('is-success');
      }
      await refreshValuation(data);
      await loadFactorExposure();
    } catch (error) { status.textContent = error.message; status.classList.add('is-error'); append(); }
  };
  rows.addEventListener('click', (event) => { if (event.target.closest('.holdings-remove')) { event.target.closest('tr').remove(); if (!rows.children.length) append(); } });

  let tickerSearchTimer = null;
  let tickerSearchAbort = null;
  const normalizedName = (value) => String(value || '').trim().toLocaleLowerCase().replace(/\s+/g, '');
  const selectInstrument = (row, item) => {
    const wrapper = row.querySelector('.holdings-ticker-search');
    const nameInput = row.querySelector('[data-field="name"]');
    const tickerInput = row.querySelector('[data-field="ticker"]');
    const badge = row.querySelector('.holdings-selected-ticker');
    if (tickerInput.value !== item.ticker) row.querySelector('[data-field="sector"]').value = '미분류';
    nameInput.value = item.name;
    tickerInput.value = item.ticker;
    wrapper.dataset.selectedName = item.name;
    badge.textContent = `자동 연결: ${item.ticker}`;
    badge.hidden = false;
    row.querySelector('[data-field="currency"]').value = /\.(KS|KQ)$/.test(item.ticker) ? 'KRW' : 'USD';
    const list = row.querySelector('.holdings-search-results');
    list.hidden = true;
    list.replaceChildren();
    classifySectors(row);
  };
  const searchInstruments = async (query, signal) => {
    const response = await fetch(`/api/home/chart-search?q=${encodeURIComponent(query)}`, { signal });
    if (!response.ok) throw new Error('종목 검색에 실패했습니다.');
    return (await response.json()).items || [];
  };
  const resolveRowTicker = async (row) => {
    const nameInput = row.querySelector('[data-field="name"]');
    const tickerInput = row.querySelector('[data-field="ticker"]');
    const query = nameInput.value.trim();
    const selectedName = row.querySelector('.holdings-ticker-search').dataset.selectedName;
    if (!query || (tickerInput.value && normalizedName(query) === normalizedName(selectedName))) return;
    const items = await searchInstruments(query);
    const exact = items.find((item) => normalizedName(item.name) === normalizedName(query));
    const selected = exact || (items.length === 1 ? items[0] : null);
    if (!selected) throw new Error(`“${query}” 종목을 하나로 결정할 수 없습니다. 검색 목록에서 종목을 선택하세요.`);
    selectInstrument(row, selected);
  };
  rows.addEventListener('input', (event) => {
    const nameInput = event.target.closest('[data-field="name"]');
    if (!nameInput) return;
    const row = nameInput.closest('tr');
    const wrapper = nameInput.closest('.holdings-ticker-search');
    const list = wrapper.querySelector('.holdings-search-results');
    const query = nameInput.value.trim();
    if (normalizedName(query) !== normalizedName(wrapper.dataset.selectedName)) {
      if (row.querySelector('[data-field="ticker"]').value) row.querySelector('[data-field="sector"]').value = '미분류';
      row.querySelector('[data-field="ticker"]').value = '';
      const badge = row.querySelector('.holdings-selected-ticker');
      badge.textContent = '';
      badge.hidden = true;
    }
    clearTimeout(tickerSearchTimer);
    tickerSearchAbort?.abort();
    list.hidden = true;
    list.replaceChildren();
    if (!query) return;
    tickerSearchTimer = setTimeout(async () => {
      tickerSearchAbort = new AbortController();
      try {
        const items = await searchInstruments(query, tickerSearchAbort.signal);
        const exact = items.find((item) => normalizedName(item.name) === normalizedName(query));
        if (exact) { selectInstrument(row, exact); return; }
        items.forEach((item) => {
          const li = document.createElement('li');
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = `${item.name} · ${item.ticker}${item.exchange ? ` (${item.exchange})` : ''}`;
          button.addEventListener('click', () => selectInstrument(row, item));
          li.appendChild(button);
          list.appendChild(li);
        });
        list.hidden = !list.childElementCount;
      } catch (error) { if (error.name !== 'AbortError') list.hidden = true; }
    }, 250);
  });
  rows.addEventListener('focusout', (event) => {
    const wrapper = event.target.closest('.holdings-ticker-search');
    if (!wrapper) return;
    setTimeout(() => { if (!wrapper.contains(document.activeElement)) wrapper.querySelector('.holdings-search-results').hidden = true; }, 150);
  });
  container.querySelector('#holdings-add').addEventListener('click', () => append());
  container.querySelector('#holdings-save').addEventListener('click', async () => {
    const button = container.querySelector('#holdings-save');
    button.disabled = true; status.textContent = '저장 중…'; status.className = 'holdings-status';
    try {
      await Promise.all([...rows.querySelectorAll('[data-holding-row]')].map(resolveRowTicker));
      await classifySectors(rows);
      const holdings = readHoldings(container);
      if (holdings.some((item) => !item.name || !item.ticker || !Number.isFinite(item.quantity) || item.quantity <= 0 || !Number.isFinite(item.average_price) || item.average_price < 0)) {
        throw new Error('각 종목의 종목명, 수량, 평균 단가를 올바르게 입력하세요.');
      }
      const data = await authRequest('/api/portfolio', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cash_krw: Number(container.querySelector('#holdings-cash').value) || 0, holdings }) });
      renderSummary(data); await refreshValuation(data); await loadFactorExposure(); status.textContent = '내 포트폴리오를 저장했습니다.'; status.classList.add('is-success');
    } catch (error) { status.textContent = error.message; status.classList.add('is-error'); } finally { button.disabled = false; }
  });
  load();
}
