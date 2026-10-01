import { api } from '../api.js';
import {
  renderFinancialDashboard,
  legendDot,
} from '../utils/financialCharts.js';
import { dataSourceNote } from './dataSourceNote.js';
import { attachStockSearch } from '../utils/stockSearch.js';
import { takePendingStock } from '../shell.js';

function escapeHtml(v) {
  return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

export function companyFinancialView(container) {
  container.innerHTML = `
    <div style="margin-bottom:24px;">
      <h1 style="font-size:1.45rem;font-weight:760;color:var(--text);margin-bottom:8px;">
        <i class="fa-solid fa-chart-pie"></i> 기업 파이낸셜 분석
      </h1>
      <p style="font-size:0.88rem;color:var(--text-muted);line-height:1.65;">
        종목명을 입력하고 목록에서 고르면 성과·수익전환·부채·어닝 차트를 한 화면에서 확인합니다.
        국내 종목은 이름만 입력하면 되고, 미국 주식은 <code>AAPL</code> 같은 티커를 입력합니다.
      </p>
    </div>
    ${dataSourceNote('real')}

    <section style="background:var(--surface-soft);border:1px solid var(--border);border-radius:12px;padding:20px 24px;margin-bottom:20px;">
      <div style="display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px;">
        <div>
          <label class="param-label">종목명 (또는 미국 티커)</label>
          <input id="cf-ticker" type="text" value="" class="param-input"
            placeholder="예: 삼성전자, 에코프로, AAPL" style="width:240px;" />
        </div>
        <div>
          <label class="param-label">기간</label>
          <div style="display:flex;border:1.5px solid var(--border);border-radius:8px;overflow:hidden;">
            <button id="cf-annual"
              style="padding:7px 16px;font-size:0.82rem;background:var(--primary);color:#111;border:none;cursor:pointer;">연간</button>
            <button id="cf-quarterly"
              style="padding:7px 16px;font-size:0.82rem;background:var(--surface);color:var(--text);border:none;cursor:pointer;">분기별</button>
          </div>
        </div>
        <button class="run-btn" id="cf-run">
          <i class="fa-solid fa-magnifying-glass"></i> 조회
        </button>
      </div>
      <p style="font-size:0.78rem;color:var(--text-subtle);margin:10px 0 0;">
        국내 종목명은 KRX 일봉 수집본의 상장 목록에서 찾습니다(코스피·코스닥 자동 구분). 재무 데이터: Yahoo Finance. 비어 있는 값은 Yahoo가 제공하지 않은 항목입니다.
      </p>
    </section>

    <div id="cf-company-header" style="display:none;margin-bottom:4px;padding:12px 18px;
      background:var(--surface);border:1px solid var(--border);border-radius:10px;
      display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;"></div>

    <div id="cf-result"></div>
  `;

  const tickerInput  = container.querySelector('#cf-ticker');
  const runBtn       = container.querySelector('#cf-run');
  const annualBtn    = container.querySelector('#cf-annual');
  const quarterlyBtn = container.querySelector('#cf-quarterly');
  const header       = container.querySelector('#cf-company-header');
  const result       = container.querySelector('#cf-result');

  let currentPeriod = 'annual';

  function setPeriod(p) {
    currentPeriod = p;
    const isAnn = p === 'annual';
    annualBtn.style.background    = isAnn ? 'var(--primary)' : 'var(--surface-soft)';
    annualBtn.style.color         = isAnn ? '#111'           : 'var(--text-muted)';
    quarterlyBtn.style.background = isAnn ? 'var(--surface-soft)' : 'var(--primary)';
    quarterlyBtn.style.color      = isAnn ? 'var(--text-muted)'   : '#111';
  }

  annualBtn.addEventListener('click',    () => setPeriod('annual'));
  quarterlyBtn.addEventListener('click', () => setPeriod('quarterly'));

  attachStockSearch(tickerInput, { onSelect: () => fetchData() });

  async function fetchData() {
    // 목록에서 고른 국내 종목은 티커를, 직접 입력한 값은 그대로(서버가 종목명이면 찾아 준다) 보낸다.
    const ticker = (tickerInput.dataset.ticker || tickerInput.value).trim();
    if (!ticker) { result.innerHTML = '<p style="color:#ef4444;">종목명을 입력하세요.</p>'; return; }

    runBtn.disabled = true;
    runBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 조회 중';
    result.innerHTML = `<p style="color:var(--text-muted);padding:20px 0;">
      <i class="fa-solid fa-spinner fa-spin"></i>&nbsp; ${escapeHtml(ticker)} 재무 데이터 로딩 중…</p>`;
    header.style.display = 'none';

    try {
      const data = await api.companyFinancials({ ticker, period: currentPeriod });

      header.style.display = 'flex';
      header.innerHTML = `
        <div>
          <span style="font-size:1.1rem;font-weight:700;color:var(--text);">${escapeHtml(data.name)}</span>
          <span style="margin-left:8px;font-size:0.82rem;color:var(--text-muted);">${escapeHtml(data.ticker)}</span>
          <span style="margin-left:6px;font-size:0.78rem;color:var(--text-subtle);">${escapeHtml(data.currency)}</span>
        </div>
        <span style="font-size:0.78rem;color:var(--text-subtle);">${data.period === 'annual' ? '연간' : '분기별'} 데이터</span>
      `;

      renderFinancialDashboard(result, data);
    } catch (err) {
      result.innerHTML = `
        <div style="background:rgba(255,59,92,.12);border:1px solid rgba(255,59,92,.35);border-radius:10px;padding:16px 20px;color:var(--red);">
          <strong>조회 실패</strong>
          <p style="margin:6px 0 0;font-size:0.85rem;">${escapeHtml(err.message)}</p>
          <p style="margin:6px 0 0;font-size:0.82rem;color:var(--text-muted);">
            예시: 국내 <code>삼성전자</code>(목록에서 선택) / 미국 <code>AAPL</code>
          </p>
        </div>`;
    } finally {
      runBtn.disabled = false;
      runBtn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> 조회';
    }
  }

  runBtn.addEventListener('click', fetchData);
  tickerInput.addEventListener('keydown', e => { if (e.key === 'Enter') fetchData(); });
  // 상단 종목 검색에서 고른 종목이 있으면 그 종목으로 바로 조회한다.
  const pending = takePendingStock();
  if (pending) { tickerInput.value = pending.name; tickerInput.dataset.ticker = pending.ticker; }
  fetchData();
}
