import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';

const money = (value) => `₩${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(Number(value) || 0)}`;
const pct = (value, digits = 2) => `${Number(value ?? 0).toFixed(digits)}%`;
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));

const PERIODS = [['3mo', '3개월'], ['6mo', '6개월'], ['1y', '1년'], ['2y', '2년'], ['3y', '3년'], ['5y', '5년']];
const STAGE_LABEL = { valuation: '지연 시세 조회 실패', history: '가격 이력 부족·조회 실패' };

function excludedList(excluded) {
  if (!excluded?.length) return '';
  const rows = excluded.map((item) => `<li><b>${escapeHtml(item.name || item.ticker)}</b> <small>${escapeHtml(item.ticker)}</small><span class="risk-exclude-stage">${STAGE_LABEL[item.stage] || '제외'}</span><p>${escapeHtml(item.reason)}</p></li>`).join('');
  return `<section class="risk-excluded"><h3><i class="fa-solid fa-triangle-exclamation"></i> 리스크 계산에서 제외된 종목</h3><ul>${rows}</ul></section>`;
}

function unavailableView(data) {
  return `
    <div class="risk-unavailable">
      <i class="fa-solid fa-circle-info"></i>
      <p>${escapeHtml(data.reason || '리스크 지표를 계산할 수 있는 데이터가 부족합니다.')}</p>
      ${data.risk_coverage_pct != null ? `<small>가격 이력 확인 비중: ${pct(data.risk_coverage_pct, 1)}</small>` : ''}
    </div>
    ${excludedList(data.excluded)}`;
}

function metricCard(label, value, note, tone = '') {
  return `<article class="risk-metric ${tone}"><span>${label}</span><strong>${value}</strong>${note ? `<small>${note}</small>` : ''}</article>`;
}

function availableView(data) {
  const currencyRows = Object.entries(data.currency_exposure || {}).map(([ccy, weight]) => `<div><span>${ccy}</span><strong>${pct(weight, 1)}</strong></div>`).join('');
  const sectorRows = Object.entries(data.sector_exposure || {}).map(([sector, weight]) => `<div><span>${escapeHtml(sector)}</span><strong>${pct(weight, 1)}</strong></div>`).join('');
  const alerts = (data.policy_alerts || []).map((alert) => `<li><b>${escapeHtml(alert.message)}</b><small>${pct(alert.actual_pct, 1)} / 한도 ${pct(alert.limit_pct, 1)}</small></li>`).join('');
  const target = data.volatility_target || {};
  const maxPosition = data.max_position;
  return `
    <section class="risk-basis-note">
      <i class="fa-solid fa-database"></i>
      <p>Yahoo Finance 일별 종가(<b>실제 데이터</b>)와 지연 시세(<b>15분 내외 지연</b>)로 계산합니다. 확률로 만든 가상 시나리오가 아닙니다 — 가상 시뮬레이션은 좌측 메뉴 <b>전략 연구소 → 리스크 분석(VaR)</b>을 참고하세요.
      USD 자산은 <b>현지통화 기준 위험</b>이며(과거 환율 변동 미반영), 결합 비중만 현재 환율로 원화 환산했습니다.</p>
    </section>
    <div class="risk-meta">
      <span>기간 <b>${data.period}</b></span>
      <span>공통 거래일 <b>${data.common_trading_days}일</b></span>
      <span>기준일 <b>${data.as_of}</b></span>
      <span>가격 이력 확인 비중 <b>${pct(data.risk_coverage_pct, 1)}</b></span>
    </div>
    <div class="risk-metric-grid">
      ${metricCard('95% Historical VaR', pct(data.var_95_pct), `1일 기준 최대 손실 추정 · ${money(data.var_95_amount_krw)}`, 'is-warn')}
      ${metricCard('95% Historical CVaR', pct(data.cvar_95_pct), `꼬리 구간 평균 손실 · ${money(data.cvar_95_amount_krw)}`, 'is-danger')}
      ${metricCard('연환산 변동성', pct(data.annual_volatility_pct), '일간 수익률 표준편차 기준')}
      ${metricCard('최대 낙폭 (MDD)', pct(data.mdd_pct), `${data.period} 구간 내 최대 하락폭`, 'is-danger')}
      ${metricCard('최대 보유 비중', maxPosition ? pct(maxPosition.weight_pct, 1) : '-', maxPosition ? `${escapeHtml(maxPosition.name)} (${escapeHtml(maxPosition.ticker)})` : '')}
      ${metricCard('평가 총액', money(data.total_value_krw), '지연 시세 기준 원화 환산')}
    </div>
    <section class="risk-currency"><h3>통화 노출</h3><div class="risk-currency-grid">${currencyRows}</div></section>
    <section class="risk-currency"><h3>업종 노출</h3><div class="risk-currency-grid">${sectorRows || '<div><span>업종 미입력</span><strong>-</strong></div>'}</div></section>
    <section class="risk-basis-note"><i class="fa-solid fa-scale-balanced"></i><p><b>위험 정책</b> ${alerts ? `</p><ul>${alerts}</ul>` : ' 모든 현재 한도가 충족됩니다.</p>'}${target.available ? `<p>목표 변동성 ${pct(target.target_annual_volatility_pct, 1)} 기준 위험자산 비중 ${pct(target.risky_asset_scale * 100, 1)}, 권고 현금 버퍼 ${pct(target.cash_buffer_pct, 1)}</p>` : ''}</section>
    ${excludedList(data.excluded)}`;
}

export function portfolioRiskView(container) {
  if (!isAuthenticated()) {
    container.innerHTML = `<section class="holdings-login"><span class="jy-section-label">RISK ANALYSIS</span><h1>실제 보유자산 리스크 분석을 보려면 로그인하세요</h1><p>보유 종목의 실제 가격 이력을 이용한 VaR·CVaR·변동성·MDD 분석은 로그인한 계정에만 제공됩니다.</p><button type="button" class="run-btn" id="risk-login"><i class="fa-solid fa-right-to-bracket"></i> 로그인하고 시작하기</button></section>`;
    container.querySelector('#risk-login').addEventListener('click', openAuthModal);
    return;
  }

  container.innerHTML = `<section class="risk-view">
    <header class="risk-head">
      <div><span class="jy-section-label">MY PORTFOLIO · RISK</span><h1>실제 보유자산 리스크 분석</h1><p>내가 등록한 보유 종목의 실제 가격 이력으로 95% VaR·CVaR·변동성·MDD를 계산합니다.</p></div>
      <div class="risk-controls">
        <label>분석 기간<select id="risk-period">${PERIODS.map(([value, label]) => `<option value="${value}" ${value === '1y' ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
        <button type="button" class="run-btn" id="risk-refresh"><i class="fa-solid fa-rotate"></i> 새로고침</button>
      </div>
    </header>
    <div id="risk-body"><p class="holdings-valuation-note">리스크를 계산하는 중…</p></div>
  </section>`;

  const body = container.querySelector('#risk-body');
  const periodSelect = container.querySelector('#risk-period');

  const load = async () => {
    body.innerHTML = '<p class="holdings-valuation-note">실제 가격 이력을 불러와 리스크를 계산하는 중…</p>';
    try {
      const data = await authRequest(`/api/portfolio/risk?period=${encodeURIComponent(periodSelect.value)}`);
      body.innerHTML = data.available ? availableView(data) : unavailableView(data);
    } catch (error) {
      body.innerHTML = `<p class="holdings-valuation-note" style="color:#dc2626">${escapeHtml(error.message)}</p>`;
    }
  };

  periodSelect.addEventListener('change', load);
  container.querySelector('#risk-refresh').addEventListener('click', load);
  load();
}
