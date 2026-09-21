import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';

const emptyHolding = () => ({ ticker: '', name: '', quantity: '', average_price: '', currency: 'KRW', sector: '미분류' });
const money = (value) => new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(Number(value) || 0);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));

export async function portfolioValuation(portfolio) {
  const holdings = portfolio.holdings || [];
  if (!holdings.length) return { available: true, value_krw: Number(portfolio.cash_krw) || 0, profit_krw: 0, return_pct: 0, priced_count: 0, holding_count: 0, positions: [] };
  return authRequest('/api/portfolio/valuation');
}

export function portfolioInsights(portfolio, valuation) {
  const total = Number(valuation.value_krw) || 0;
  const positions = [
    ...(Number(portfolio.cash_krw) > 0 ? [{ ticker: 'CASH', name: '현금', currency: 'KRW', value_krw: Number(portfolio.cash_krw), profit_krw: 0 }] : []),
    ...(valuation.positions || []),
  ].map((item) => ({ ...item, weight_pct: total ? item.value_krw / total * 100 : 0 })).sort((a, b) => b.value_krw - a.value_krw);
  const currency = positions.reduce((result, item) => { result[item.currency] = (result[item.currency] || 0) + item.value_krw; return result; }, {});
  const maxPosition = positions[0] || null;
  const hhi = positions.reduce((sum, item) => sum + (item.weight_pct / 100) ** 2, 0);
  return { total, positions, currency, max_position: maxPosition, hhi, concentration: hhi >= 0.45 ? '높음' : hhi >= 0.25 ? '보통' : '낮음' };
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
    <td><input data-field="sector" value="${escapeHtml(holding.sector || '미분류')}" placeholder="예: 반도체" maxlength="40" aria-label="업종"></td>
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

function factorExposureHtml(data) {
  if (!data.available) return `<header><div><span class="jy-section-label">FACTOR EXPOSURE</span><h2>보유 팩터 노출</h2></div></header><p class="holdings-valuation-note">${escapeHtml(data.reason || '팩터 데이터를 준비 중입니다.')}</p>`;
  const factors = data.weighted_factors || {};
  const pct = (value) => value == null ? '-' : `${(Number(value) * 100).toFixed(2)}%`;
  const rows = (data.positions || []).map((position) => `<tr><td>${escapeHtml(position.name || position.ticker)}</td><td>${position.weight_pct.toFixed(1)}%</td><td>${pct(position.factors.return_20d)}</td><td>${pct(position.factors.realized_volatility_20d)}</td><td>${pct(position.factors.max_drawdown_60d)}</td></tr>`).join('');
  return `<header><div><span class="jy-section-label">FACTOR EXPOSURE</span><h2>보유 팩터 노출</h2></div><small>${escapeHtml(data.as_of)} 기준 · 종목 커버리지 ${data.coverage_pct}%</small></header><div class="holdings-insight-grid"><article><span>20일 수익률</span><strong>${pct(factors.return_20d)}</strong><small>보유비중 가중 관측값</small></article><article><span>20일 변동성</span><strong>${pct(factors.realized_volatility_20d)}</strong><small>연율화 실현변동성</small></article><article><span>60일 최대낙폭</span><strong>${pct(factors.max_drawdown_60d)}</strong><small>보유비중 가중 관측값</small></article></div><div class="holdings-table-wrap"><table><thead><tr><th>종목</th><th>비중</th><th>20일 수익률</th><th>20일 변동성</th><th>60일 MDD</th></tr></thead><tbody>${rows}</tbody></table></div><p class="holdings-valuation-note">${escapeHtml(data.disclaimer)}</p>`;
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
    <section class="holdings-editor"><header><h2>보유 내역 입력</h2><p>종목명만 입력하세요. 정확히 일치하는 종목은 자동으로 연결되며, 이름이 비슷한 종목이 여러 개면 검색 목록에서 선택할 수 있습니다. 업종을 입력하면 리스크 화면에서 업종별 쏠림도 점검합니다.</p></header><label class="holdings-cash-label">보유 현금 (KRW)<input id="holdings-cash" type="number" min="0" step="1000" value="0"></label><div class="holdings-table-wrap"><table><thead><tr><th>종목명</th><th>수량</th><th>평균 단가</th><th>통화</th><th>업종</th><th></th></tr></thead><tbody id="holdings-rows"></tbody></table></div><div class="holdings-actions"><button type="button" id="holdings-add"><i class="fa-solid fa-plus"></i> 종목 추가</button><button type="button" class="run-btn" id="holdings-save"><i class="fa-solid fa-floppy-disk"></i> 포트폴리오 저장</button></div><p id="holdings-status" class="holdings-status" aria-live="polite"></p></section>
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
    const source = valuation.source === 'market_data_cache_daily' ? '검증 캐시 · 일봉 종가' : '지연 시세·검증 캐시 혼합';
    const asOf = (valuation.positions || []).map((item) => item.quoted_at).filter(Boolean).sort().at(-1);
    note.textContent = `${source}${asOf ? ` · 기준일 ${asOf.slice(0, 10)}` : ''} · ${valuation.priced_count}개 종목 평가 완료 · USD 자산은 USD/KRW 환율로 원화 환산`;
  };
  const renderInsights = (data, valuation) => {
    const insight = portfolioInsights(data, valuation);
    const root = container.querySelector('#holdings-insights');
    if (!valuation.available || (!valuation.positions?.length && !Number(data.cash_krw))) { root.hidden = true; return; }
    const currencies = Object.entries(insight.currency).map(([currency, value]) => `<div><span>${currency}</span><strong>${(value / insight.total * 100).toFixed(1)}%</strong></div>`).join('');
    const positions = insight.positions.slice(0, 6).map((item) => `<li><span>${escapeHtml(item.name || item.ticker)} <small>${escapeHtml(item.ticker)}</small></span><b>${item.weight_pct.toFixed(1)}%</b><i><em style="width:${Math.min(item.weight_pct, 100)}%"></em></i></li>`).join('');
    root.innerHTML = `<header><div><span class="jy-section-label">PORTFOLIO INSIGHTS</span><h2>비중과 집중도</h2></div><span class="holdings-concentration is-${insight.concentration === '높음' ? 'high' : insight.concentration === '보통' ? 'mid' : 'low'}">집중도 ${insight.concentration}</span></header><div class="holdings-insight-grid"><article><span>최대 비중</span><strong>${insight.max_position ? `${escapeHtml(insight.max_position.name || insight.max_position.ticker)} ${insight.max_position.weight_pct.toFixed(1)}%` : '-'}</strong><small>현금도 하나의 자산으로 포함합니다.</small></article><article><span>통화 노출</span><div class="holdings-currency">${currencies}</div><small>USD는 현재 환율 기준 원화 환산입니다.</small></article></div><ol class="holdings-weight-list">${positions}</ol>`;
    root.hidden = false;
  };
  const loadFactorExposure = async () => {
    const root = container.querySelector('#holdings-factor-exposure');
    try { const data = await authRequest('/api/portfolio/factor-exposure'); root.hidden = false; root.innerHTML = factorExposureHtml(data); } catch { root.hidden = false; root.innerHTML = factorExposureHtml({ available: false, reason: '팩터 노출 데이터를 불러오지 못했습니다.' }); }
  };
  const load = async () => {
    try {
      const data = await authRequest('/api/portfolio');
      container.querySelector('#holdings-cash').value = data.cash_krw || 0;
      (data.holdings?.length ? data.holdings : [emptyHolding()]).forEach(append);
      renderSummary(data);
      try { const valuation = await portfolioValuation(data); renderValuation(valuation); renderInsights(data, valuation); } catch { container.querySelector('#holdings-valuation-note').textContent = '시세를 불러오지 못해 장부 기준 금액을 표시합니다.'; }
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
    nameInput.value = item.name;
    tickerInput.value = item.ticker;
    wrapper.dataset.selectedName = item.name;
    badge.textContent = `자동 연결: ${item.ticker}`;
    badge.hidden = false;
    row.querySelector('[data-field="currency"]').value = /\.(KS|KQ)$/.test(item.ticker) ? 'KRW' : 'USD';
    const list = row.querySelector('.holdings-search-results');
    list.hidden = true;
    list.replaceChildren();
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
      const holdings = readHoldings(container);
      if (holdings.some((item) => !item.name || !item.ticker || !Number.isFinite(item.quantity) || item.quantity <= 0 || !Number.isFinite(item.average_price) || item.average_price < 0)) {
        throw new Error('각 종목의 종목명, 수량, 평균 단가를 올바르게 입력하세요.');
      }
      const data = await authRequest('/api/portfolio', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cash_krw: Number(container.querySelector('#holdings-cash').value) || 0, holdings }) });
      renderSummary(data); try { const valuation = await portfolioValuation(data); renderValuation(valuation); renderInsights(data, valuation); } catch {} await loadFactorExposure(); status.textContent = '내 포트폴리오를 저장했습니다.'; status.classList.add('is-success');
    } catch (error) { status.textContent = error.message; status.classList.add('is-error'); } finally { button.disabled = false; }
  });
  load();
}
