import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';

const emptyHolding = () => ({ ticker: '', name: '', quantity: '', average_price: '', currency: 'KRW' });
const money = (value) => new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(Number(value) || 0);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));

export async function portfolioValuation(portfolio) {
  const holdings = portfolio.holdings || [];
  if (!holdings.length) return { available: true, value_krw: Number(portfolio.cash_krw) || 0, profit_krw: 0, return_pct: 0, priced_count: 0, holding_count: 0, positions: [] };
  const tickers = [...new Set([...holdings.map((item) => item.ticker), ...(holdings.some((item) => item.currency === 'USD') ? ['KRW=X'] : [])])];
  const response = await fetch('/api/market/snapshot', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tickers }) });
  if (!response.ok) throw new Error('시세를 불러오지 못했습니다.');
  const quoteByTicker = new Map((await response.json()).items.filter((item) => item.status === 'ok').map((item) => [item.ticker, Number(item.value)]));
  const usdKrw = quoteByTicker.get('KRW=X');
  let valueKrw = Number(portfolio.cash_krw) || 0;
  let costKrw = Number(portfolio.cash_krw) || 0;
  let pricedCount = 0;
  const positions = [];
  holdings.forEach((item) => {
    const price = quoteByTicker.get(item.ticker);
    const fx = item.currency === 'USD' ? usdKrw : 1;
    if (!Number.isFinite(price) || !Number.isFinite(fx)) return;
    valueKrw += price * item.quantity * fx;
    costKrw += item.average_price * item.quantity * fx;
    pricedCount += 1;
    positions.push({ ...item, value_krw: price * item.quantity * fx, cost_krw: item.average_price * item.quantity * fx, profit_krw: (price - item.average_price) * item.quantity * fx });
  });
  const profitKrw = valueKrw - costKrw;
  return { available: pricedCount === holdings.length, value_krw: valueKrw, profit_krw: profitKrw, return_pct: costKrw ? profitKrw / costKrw * 100 : 0, priced_count: pricedCount, holding_count: holdings.length, positions };
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
    <td><input data-field="ticker" value="${escapeHtml(holding.ticker)}" placeholder="005930.KS" maxlength="20" aria-label="티커"></td>
    <td><div class="holdings-ticker-search"><input data-field="name" value="${escapeHtml(holding.name)}" placeholder="종목명 입력 (자동 검색)" maxlength="80" autocomplete="off" aria-label="종목명 검색"><ul class="holdings-search-results" hidden></ul></div></td>
    <td><input data-field="quantity" type="number" min="0.000001" step="any" value="${escapeHtml(holding.quantity)}" aria-label="수량"></td>
    <td><input data-field="average_price" type="number" min="0" step="any" value="${escapeHtml(holding.average_price)}" aria-label="평균 단가"></td>
    <td><select data-field="currency" aria-label="통화"><option value="KRW" ${holding.currency === 'KRW' ? 'selected' : ''}>KRW</option><option value="USD" ${holding.currency === 'USD' ? 'selected' : ''}>USD</option></select></td>
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
  })).filter((holding) => holding.ticker || holding.name || holding.quantity || holding.average_price);
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
    <section class="holdings-editor"><header><h2>보유 내역 입력</h2><p>종목명을 입력하면 티커가 자동으로 채워집니다. 직접 검색되지 않으면 국내 종목은 <b>005930.KS</b>, 미국 종목은 <b>AAPL</b> 형식으로 티커를 입력하세요.</p></header><label class="holdings-cash-label">보유 현금 (KRW)<input id="holdings-cash" type="number" min="0" step="1000" value="0"></label><div class="holdings-table-wrap"><table><thead><tr><th>티커</th><th>종목명</th><th>수량</th><th>평균 단가</th><th>통화</th><th></th></tr></thead><tbody id="holdings-rows"></tbody></table></div><div class="holdings-actions"><button type="button" id="holdings-add"><i class="fa-solid fa-plus"></i> 종목 추가</button><button type="button" class="run-btn" id="holdings-save"><i class="fa-solid fa-floppy-disk"></i> 포트폴리오 저장</button></div><p id="holdings-status" class="holdings-status" aria-live="polite"></p></section>
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
    container.querySelector('#holdings-book-value').textContent = `₩${money(valuation.value_krw)}`;
    profit.textContent = `${valuation.profit_krw >= 0 ? '+' : '-'}₩${money(Math.abs(valuation.profit_krw))} (${valuation.return_pct >= 0 ? '+' : ''}${valuation.return_pct.toFixed(2)}%)`;
    profit.className = valuation.profit_krw >= 0 ? 'is-profit' : 'is-loss';
    note.textContent = valuation.available
      ? `Yahoo Finance 지연 시세 기준 · ${valuation.priced_count}개 종목 평가 완료${valuation.holding_count > valuation.priced_count ? ` (${valuation.holding_count - valuation.priced_count}개 종목은 제외)` : ''} · USD 자산은 현재 USD/KRW 환율로 원화 환산`
      : `일부 시세를 불러오지 못했습니다. ${valuation.priced_count}/${valuation.holding_count}개 종목만 평가에 반영했습니다.`;
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
  const load = async () => {
    try {
      const data = await authRequest('/api/portfolio');
      container.querySelector('#holdings-cash').value = data.cash_krw || 0;
      (data.holdings?.length ? data.holdings : [emptyHolding()]).forEach(append);
      renderSummary(data);
      try { const valuation = await portfolioValuation(data); renderValuation(valuation); renderInsights(data, valuation); } catch { container.querySelector('#holdings-valuation-note').textContent = '시세를 불러오지 못해 장부 기준 금액을 표시합니다.'; }
    } catch (error) { status.textContent = error.message; status.classList.add('is-error'); append(); }
  };
  rows.addEventListener('click', (event) => { if (event.target.closest('.holdings-remove')) { event.target.closest('tr').remove(); if (!rows.children.length) append(); } });

  let tickerSearchTimer = null;
  let tickerSearchAbort = null;
  rows.addEventListener('input', (event) => {
    const nameInput = event.target.closest('[data-field="name"]');
    if (!nameInput) return;
    const list = nameInput.nextElementSibling;
    const query = nameInput.value.trim();
    clearTimeout(tickerSearchTimer);
    tickerSearchAbort?.abort();
    list.hidden = true;
    list.replaceChildren();
    if (!query) return;
    tickerSearchTimer = setTimeout(async () => {
      tickerSearchAbort = new AbortController();
      try {
        const response = await fetch(`/api/home/chart-search?q=${encodeURIComponent(query)}`, { signal: tickerSearchAbort.signal });
        if (!response.ok) return;
        const data = await response.json();
        (data.items || []).forEach((item) => {
          const li = document.createElement('li');
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = `${item.name} · ${item.ticker}${item.exchange ? ` (${item.exchange})` : ''}`;
          button.addEventListener('click', () => {
            const row = nameInput.closest('tr');
            nameInput.value = item.name;
            row.querySelector('[data-field="ticker"]').value = item.ticker;
            row.querySelector('[data-field="currency"]').value = /\.(KS|KQ)$/.test(item.ticker) ? 'KRW' : 'USD';
            list.hidden = true;
            list.replaceChildren();
          });
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
    const holdings = readHoldings(container);
    if (holdings.some((item) => !item.ticker || !Number.isFinite(item.quantity) || item.quantity <= 0 || !Number.isFinite(item.average_price) || item.average_price < 0)) {
      status.textContent = '각 종목의 티커, 수량, 평균 단가를 올바르게 입력하세요.'; status.className = 'holdings-status is-error'; return;
    }
    button.disabled = true; status.textContent = '저장 중…'; status.className = 'holdings-status';
    try {
      const data = await authRequest('/api/portfolio', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cash_krw: Number(container.querySelector('#holdings-cash').value) || 0, holdings }) });
      renderSummary(data); try { const valuation = await portfolioValuation(data); renderValuation(valuation); renderInsights(data, valuation); } catch {} status.textContent = '내 포트폴리오를 저장했습니다.'; status.classList.add('is-success');
    } catch (error) { status.textContent = error.message; status.classList.add('is-error'); } finally { button.disabled = false; }
  });
  load();
}
