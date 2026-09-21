import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';

const money = (value) => `₩${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(Number(value) || 0)}`;
const num = (value, digits = 4) => Number(value ?? 0).toFixed(digits).replace(/\.?0+$/, '') || '0';
const pct = (value, digits = 2) => (value == null ? '-' : `${Number(value).toFixed(digits)}%`);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));

const STATUS_LABEL = {
  bought: '오늘 매수 실행', sold: '매도 트리거 발동', no_action: '조건상 매수 없음',
  disabled: '자동매매 꺼짐', blocked: '주문 차단됨', signals_unavailable: '신호 데이터 없음',
  pricing_unavailable: '시세 조회 실패', error: '실행 오류', already_run_today: '오늘 실행 완료(중복 방지)',
};
const ACTION_STATUS_LABEL = { filled: '체결', blocked: '한도 차단', skipped: '건너뜀' };

function statusBadge(status) {
  const tone = status === 'bought' || status === 'sold' ? 'is-success' : (status === 'blocked' || status === 'error' || status === 'pricing_unavailable') ? 'is-error' : 'is-muted';
  return `<span class="lvdca-badge ${tone}">${escapeHtml(STATUS_LABEL[status] || status || '-')}</span>`;
}

function signalsHtml(signals) {
  if (!signals) return '<p class="lvdca-empty">아직 신호를 불러오지 않았습니다.</p>';
  if (!signals.available) {
    return `<p class="lvdca-empty">신호 데이터를 불러오지 못했습니다: ${escapeHtml((signals.errors || []).join('; ') || '알 수 없는 오류')}</p>`;
  }
  return `
    <div class="lvdca-signal-grid">
      <div><span>S&amp;P 500 52주 고점 대비</span><strong class="${signals.spx_drawdown_pct <= -8 ? 'is-danger' : ''}">${pct(signals.spx_drawdown_pct)}</strong></div>
      <div><span>VIX</span><strong class="${signals.vix >= 35 ? 'is-danger' : ''}">${num(signals.vix, 2)}</strong></div>
      <div><span>SOXL 52주 고점 대비</span><strong>${pct(signals.soxl_drawdown_pct)}</strong></div>
      <div><span>UPRO 52주 고점 대비</span><strong>${pct(signals.upro_drawdown_pct)}</strong></div>
      <div><span>S&amp;P 50일선 위 연속일</span><strong>${signals.spx_above_ma_consecutive_days ?? '-'}일</strong></div>
      <div><span>기준일</span><strong>${escapeHtml(signals.as_of || '-')}</strong></div>
    </div>
    ${signals.errors?.length ? `<p class="lvdca-note is-warn">일부 신호 조회 오류: ${escapeHtml(signals.errors.join('; '))}</p>` : ''}`;
}

function actionsHtml(actions) {
  if (!actions?.length) return '<p class="lvdca-empty">오늘 실행된 매수·매도가 없습니다.</p>';
  const rows = actions.map((action) => `
    <tr>
      <td>${escapeHtml(action.ticker)}</td>
      <td>${action.direction === 'buy' ? '매수' : '매도'}</td>
      <td>${escapeHtml(ACTION_STATUS_LABEL[action.status] || action.status)}</td>
      <td>${action.quantity != null ? num(action.quantity, 6) : '-'}</td>
      <td>${action.notional_krw != null ? money(action.notional_krw) : '-'}</td>
      <td>${escapeHtml(action.reason || '-')}</td>
    </tr>`).join('');
  return `<div class="lvdca-table-wrap"><table><thead><tr><th>종목</th><th>구분</th><th>결과</th><th>수량</th><th>금액</th><th>사유</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function tradesHtml(items) {
  if (!items?.length) return '<p class="lvdca-empty">아직 체결된 매수·매도가 없습니다.</p>';
  const rows = items.map((item) => `
    <tr>
      <td>${escapeHtml(item.date)}</td>
      <td>${escapeHtml(item.ticker)}${item.is_reserve ? ' <small>(예비비)</small>' : ''}</td>
      <td class="${item.direction === 'buy' ? 'is-up' : 'is-down'}">${item.direction === 'buy' ? '매수' : '매도'}</td>
      <td>${num(item.quantity, 6)}주</td>
      <td>$${num(item.fill_price, 4)}</td>
      <td>${money(item.notional_krw)}</td>
    </tr>`).join('');
  return `<div class="lvdca-table-wrap"><table><thead><tr><th>일시(KST)</th><th>종목</th><th>구분</th><th>수량</th><th>체결가</th><th>금액</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function positionsHtml(positions) {
  if (!positions?.length) return '<p class="lvdca-empty">현재 모의 보유 포지션이 없습니다.</p>';
  const rows = positions.map((item) => `
    <div class="lvdca-position"><span>${escapeHtml(item.ticker)}</span><strong>${num(item.quantity, 4)}주</strong><small>평균단가 ${money(item.average_cost_krw)}</small></div>`).join('');
  return `<div class="lvdca-position-grid">${rows}</div>`;
}

function reserveHtml(reserve, reserveResult) {
  if (!reserve?.enabled) {
    return '<p class="lvdca-empty">예비비 자동 투입이 꺼져 있습니다. 위 설정에서 켤 수 있습니다.</p>';
  }
  const checks = reserveResult?.checks;
  const checkRows = checks ? Object.entries({
    fed_not_hiking: '연준 금리 인상 아님', vix_extreme: 'VIX 극단적 공포(≥50)', ten_year_falling: '10년물 금리 하락',
    no_sector_specific_collapse: 'SOXL 단독 붕괴 아님', labor_weak: '고용 지표 약화', fast_drawdown: '30일 이내 급락',
  }).map(([key, label]) => `<li class="${checks[key] ? 'is-ok' : 'is-fail'}"><i class="fa-solid ${checks[key] ? 'fa-circle-check' : 'fa-circle-xmark'}"></i>${label}</li>`).join('') : '';
  return `
    <div class="lvdca-metric-grid">
      <article class="lvdca-metric"><span>투입 단계</span><strong>${reserve.deployed_stage}/3</strong></article>
      <article class="lvdca-metric"><span>투입 금액</span><strong>${money(reserve.deployed_krw)}</strong></article>
      <article class="lvdca-metric"><span>예비비 총액</span><strong>${money(reserve.reserve_cash_krw)}</strong></article>
      <article class="lvdca-metric"><span>현재 평가액</span><strong>${money(reserve.market_value_krw)}</strong></article>
    </div>
    ${reserve.pricing_errors?.length ? `<p class="lvdca-note is-warn">시세 조회 오류: ${escapeHtml(reserve.pricing_errors.join('; '))}</p>` : ''}
    ${positionsHtml(reserve.positions)}
    ${checkRows ? `<ul class="lvdca-check-list">${checkRows}</ul>` : '<p class="lvdca-empty">아직 예비비 판정 기록이 없습니다.</p>'}
    <p class="lvdca-config-note">6개 지표를 모두 충족해야 투입 후보가 되며, S&amp;P 낙폭 -20/-30/-40%에 따라 1~3단계로 나뉩니다. UPRO만 매수하며 자동 매도는 하지 않습니다.</p>`;
}

function scheduleHtml(schedule) {
  if (!schedule?.market_open_today) return '<p class="lvdca-empty">오늘은 NYSE 휴장일이라 자동 실행이 없습니다.</p>';
  const fmt = (iso) => (iso ? new Date(iso).toLocaleString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false }) : '-');
  return `<div class="lvdca-signal-grid">
    <div><span>매수 실행(장마감 20분 전)</span><strong>${fmt(schedule.buy_time_kst)} KST</strong></div>
    <div><span>장 마감</span><strong>${fmt(schedule.market_close_kst)} KST</strong></div>
    <div><span>매도 체크(마감 30분 후)</span><strong>${fmt(schedule.sell_check_time_kst)} KST</strong></div>
  </div>`;
}

function statusView(status) {
  const log = status.latest_log;
  return `
    ${status.buy_stopped ? '<p class="lvdca-badge is-error"><i class="fa-solid fa-hand"></i> 현재 주전략 매수 중지 중 — 재개 조건(50일선 위 3일 연속 + VIX 안정) 충족 전까지 유지됩니다.</p>' : ''}
    <section class="lvdca-section"><h3><i class="fa-solid fa-clock"></i> 오늘 실행 스케줄 (서머타임 자동 반영)</h3>${scheduleHtml(status.execution_schedule)}</section>
    <div class="lvdca-metric-grid">
      <article class="lvdca-metric"><span>자동매매 상태</span><strong>${status.enabled ? '켜짐' : '꺼짐'}</strong></article>
      <article class="lvdca-metric"><span>누적 매수 원가</span><strong>${money(status.cost_basis_krw)}</strong></article>
      <article class="lvdca-metric"><span>현재 평가액</span><strong>${money(status.market_value_krw)}</strong></article>
      <article class="lvdca-metric ${status.market_value_krw - status.cost_basis_krw >= 0 ? 'is-up' : 'is-down'}"><span>평가손익</span><strong>${money(status.market_value_krw - status.cost_basis_krw)}</strong></article>
    </div>
    ${status.pricing_errors?.length ? `<p class="lvdca-note is-warn">시세 조회 오류: ${escapeHtml(status.pricing_errors.join('; '))}</p>` : ''}
    <section class="lvdca-section"><h3><i class="fa-solid fa-layer-group"></i> 현재 모의 보유</h3>${positionsHtml(status.positions)}</section>
    <section class="lvdca-section">
      <h3><i class="fa-solid fa-piggy-bank"></i> 예비비 단계 투입</h3>
      ${reserveHtml(status.reserve, log?.result?.reserve?.reserve_result)}
    </section>
    <section class="lvdca-section">
      <h3><i class="fa-solid fa-clock-rotate-left"></i> 최근 실행 기록 ${log ? `<small>(${escapeHtml(log.snapshot_date)})</small>` : ''}</h3>
      ${log ? `${statusBadge(log.result?.status)}${signalsHtml(log.signals)}${actionsHtml(log.result?.actions)}` : '<p class="lvdca-empty">아직 실행 기록이 없습니다. 아래 "지금 실행"으로 첫 사이클을 모의 실행해 보세요.</p>'}
    </section>`;
}

export function leverageDcaView(container) {
  if (!isAuthenticated()) {
    container.innerHTML = `<section class="holdings-login"><span class="jy-section-label">LEVERAGE DCA · PAPER ONLY</span><h1>레버리지 ETF 자동 모의매매를 보려면 로그인하세요</h1><p>UPRO/SOXL DCA 신호 계산과 모의 체결 현황은 로그인한 계정에만 제공됩니다.</p><button type="button" class="run-btn" id="lvdca-login"><i class="fa-solid fa-right-to-bracket"></i> 로그인하고 시작하기</button></section>`;
    container.querySelector('#lvdca-login').addEventListener('click', openAuthModal);
    return;
  }

  container.innerHTML = `<section class="lvdca-view">
    <header class="lvdca-head">
      <div>
        <span class="jy-section-label">STRATEGY · PAPER TRADING ONLY</span>
        <h1>UPRO/SOXL 레버리지 DCA 자동 모의매매</h1>
        <p>미국 3배 레버리지 ETF <b>UPRO(S&amp;P500)</b>·<b>SOXL(반도체)</b> 2종목 한정 전략입니다. 실거래·브로커 주문은 만들지 않으며, 모든 체결은 <b>모의 계좌(paper_fills)</b>에만 기록됩니다.</p>
      </div>
      <button type="button" class="run-btn" id="lvdca-run-now"><i class="fa-solid fa-bolt"></i> 지금 실행 (테스트)</button>
    </header>

    <section class="risk-basis-note lvdca-warning">
      <i class="fa-solid fa-triangle-exclamation"></i>
      <p><b>고위험 레버리지 상품 안내.</b> 3배 레버리지 ETF는 변동성 소멸(Volatility Decay)로 장기 보유 시 손실이 커질 수 있습니다.
      이 화면과 자동 실행은 <b>실거래 권한을 만들지 않는 교육·연구용 모의 체결</b>이며, 실제 투자 권유가 아닙니다.</p>
    </section>

    <section class="lvdca-config">
      <h2><i class="fa-solid fa-sliders"></i> 자동매매 설정</h2>
      <form id="lvdca-config-form">
        <label class="lvdca-toggle"><input type="checkbox" id="lvdca-enabled" /> <span>자동 모의매매 켜기</span></label>
        <div class="lvdca-config-grid">
          <label>일일 매수금액(원)<input type="number" id="lvdca-daily-buy" min="100000" step="100000" required /></label>
          <label>최대 누적 매수(원)<input type="number" id="lvdca-max-position" min="1000000" step="1000000" required /></label>
          <label>매도 목표 실수령(원)<input type="number" id="lvdca-sell-target" min="100000" step="100000" required /></label>
        </div>
        <p class="lvdca-config-note">UPRO 60% / SOXL 40% 비중으로 자동 분할 매수합니다. 켜두면 NYSE 장마감 20분 전(서머타임 자동 반영, 휴장일 제외)에 하루 한 번 신호를 계산해 모의 체결합니다.</p>
        <hr class="lvdca-divider" />
        <label class="lvdca-toggle"><input type="checkbox" id="lvdca-reserve-enabled" /> <span>예비비 단계 투입 켜기</span></label>
        <div class="lvdca-config-grid">
          <label>예비비 총액(원)<input type="number" id="lvdca-reserve-cash" min="1000000" step="1000000" required /></label>
        </div>
        <p class="lvdca-config-note">연준 금리·VIX·10년물 금리·SOXL 괴리·고용지표·낙폭속도 6개 지표가 모두 충족되면 S&amp;P 낙폭 -20/-30/-40%에 맞춰 예비비를 1~3단계로 나눠 UPRO만 매수합니다. 자동 매도는 하지 않습니다.</p>
        <hr class="lvdca-divider" />
        <label class="lvdca-toggle"><input type="checkbox" id="lvdca-alpaca-enabled" /> <span>Alpaca 페이퍼 브로커로 체결하기</span></label>
        <p class="lvdca-config-note" id="lvdca-alpaca-note">서버에 <code>ALPACA_PAPER_API_KEY</code>/<code>ALPACA_PAPER_API_SECRET</code>가 설정된 경우에만 동작합니다. Alpaca의 자체 모의 계좌(가짜 자금)로 지정가→시장가 전환을 실제 주문 엔진에서 체결하며, 키가 없거나 오류가 나면 자동으로 자체 모의 체결로 대체됩니다. 실거래 엔드포인트는 절대 호출하지 않습니다.</p>
        <button type="submit" class="run-btn" id="lvdca-save"><i class="fa-solid fa-floppy-disk"></i> 설정 저장</button>
        <span id="lvdca-config-status" class="lvdca-config-status"></span>
      </form>
    </section>

    <div id="lvdca-status-body"><p class="lvdca-empty">현황을 불러오는 중…</p></div>

    <section class="lvdca-section">
      <h3><i class="fa-solid fa-list"></i> 매수·매도 내역</h3>
      <div id="lvdca-trades-body"><p class="lvdca-empty">내역을 불러오는 중…</p></div>
    </section>
  </section>`;

  const enabledInput = container.querySelector('#lvdca-enabled');
  const dailyBuyInput = container.querySelector('#lvdca-daily-buy');
  const maxPositionInput = container.querySelector('#lvdca-max-position');
  const sellTargetInput = container.querySelector('#lvdca-sell-target');
  const reserveEnabledInput = container.querySelector('#lvdca-reserve-enabled');
  const reserveCashInput = container.querySelector('#lvdca-reserve-cash');
  const alpacaEnabledInput = container.querySelector('#lvdca-alpaca-enabled');
  const alpacaNote = container.querySelector('#lvdca-alpaca-note');
  const configStatus = container.querySelector('#lvdca-config-status');
  const statusBody = container.querySelector('#lvdca-status-body');
  const tradesBody = container.querySelector('#lvdca-trades-body');
  const runButton = container.querySelector('#lvdca-run-now');

  const loadConfig = async () => {
    try {
      const config = await authRequest('/api/strategy/leverage-dca/config');
      enabledInput.checked = Boolean(config.enabled);
      dailyBuyInput.value = config.policy.daily_buy_krw;
      maxPositionInput.value = config.policy.max_position_krw;
      sellTargetInput.value = config.policy.sell_net_profit_target_krw;
      reserveEnabledInput.checked = Boolean(config.reserve_enabled);
      reserveCashInput.value = config.policy.reserve_cash_krw;
      alpacaEnabledInput.checked = Boolean(config.alpaca_enabled);
      alpacaNote.classList.toggle('is-warn', !config.alpaca_paper_configured);
      if (!config.alpaca_paper_configured) {
        alpacaNote.textContent = '서버에 Alpaca 페이퍼 API 키가 설정되지 않아, 켜도 자체 모의 체결로만 동작합니다.';
      }
    } catch (error) {
      configStatus.textContent = error.message;
      configStatus.className = 'lvdca-config-status is-error';
    }
  };

  const loadStatus = async () => {
    try {
      const status = await authRequest('/api/strategy/leverage-dca/status');
      statusBody.innerHTML = statusView(status);
    } catch (error) {
      statusBody.innerHTML = `<p class="lvdca-empty" style="color:var(--red)">${escapeHtml(error.message)}</p>`;
    }
  };

  const loadTrades = async () => {
    try {
      const trades = await authRequest('/api/strategy/leverage-dca/trades');
      tradesBody.innerHTML = tradesHtml(trades.items);
    } catch (error) {
      tradesBody.innerHTML = `<p class="lvdca-empty" style="color:var(--red)">${escapeHtml(error.message)}</p>`;
    }
  };

  container.querySelector('#lvdca-config-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    configStatus.textContent = '저장하는 중…';
    configStatus.className = 'lvdca-config-status';
    try {
      await authRequest('/api/strategy/leverage-dca/config', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: enabledInput.checked,
          daily_buy_krw: Number(dailyBuyInput.value),
          max_position_krw: Number(maxPositionInput.value),
          sell_net_profit_target_krw: Number(sellTargetInput.value),
          reserve_enabled: reserveEnabledInput.checked,
          reserve_cash_krw: Number(reserveCashInput.value),
          alpaca_enabled: alpacaEnabledInput.checked,
        }),
      });
      configStatus.textContent = '저장했습니다.';
      configStatus.className = 'lvdca-config-status is-success';
      await loadStatus();
    } catch (error) {
      configStatus.textContent = error.message;
      configStatus.className = 'lvdca-config-status is-error';
    }
  });

  runButton.addEventListener('click', async () => {
    runButton.disabled = true;
    runButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 실행 중…';
    try {
      await authRequest('/api/strategy/leverage-dca/run-now', { method: 'POST' });
      await Promise.all([loadStatus(), loadTrades()]);
    } catch (error) {
      statusBody.innerHTML = `<p class="lvdca-empty" style="color:var(--red)">${escapeHtml(error.message)}</p>`;
    } finally {
      runButton.disabled = false;
      runButton.innerHTML = '<i class="fa-solid fa-bolt"></i> 지금 실행 (테스트)';
    }
  });

  loadConfig();
  loadStatus();
  loadTrades();
}
