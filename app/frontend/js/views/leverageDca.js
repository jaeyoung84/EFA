import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';

const money = (value) => `₩${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(Number(value) || 0)}`;
const num = (value, digits = 4) => Number(value ?? 0).toFixed(digits).replace(/\.?0+$/, '') || '0';
const pct = (value, digits = 2) => (value == null ? '-' : `${Number(value).toFixed(digits)}%`);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));

const STATUS_LABEL = {
  bought: '오늘 매수 실행', sold: '매도 트리거 발동', no_action: '조건상 매수 없음',
  disabled: '자동매매 꺼짐', blocked: '주문 차단됨', signals_unavailable: '신호 데이터 없음',
  pricing_unavailable: '시세 조회 실패', error: '실행 오류', already_run_today: '오늘 실행 완료(중복 방지)',
  signals_stale: '시세 지연으로 판정 보류',
};
const ACTION_STATUS_LABEL = { filled: '체결', blocked: '한도 차단', skipped: '건너뜀' };

function statusBadge(status) {
  const tone = status === 'bought' || status === 'sold' ? 'is-success' : (status === 'blocked' || status === 'error' || status === 'pricing_unavailable' || status === 'signals_stale') ? 'is-error' : 'is-muted';
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
      <div><span>시세 기준일</span><strong class="${signals.stale ? 'is-danger' : ''}">${escapeHtml(signals.as_of || '-')}</strong></div>
    </div>
    ${signals.data_dates ? `<p class="lvdca-note${signals.stale ? ' is-warn' : ''}">${signals.stale ? '시세가 오래되어 오늘 판정을 보류합니다 · ' : ''}마지막 일봉: ${Object.entries(signals.data_dates).map(([ticker, day]) => `${escapeHtml(ticker)} ${escapeHtml(day)}`).join(' · ')}</p>` : ''}
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

const ALPACA_STATUS_LABEL = { filled: '체결', partially_filled: '부분 체결', error: '오류', rejected: '거부', unfilled: '미체결' };

function alpacaTradesHtml(items, configured) {
  if (!configured) {
    return '<p class="lvdca-empty">서버에 Alpaca 페이퍼 API 키가 설정되지 않았습니다. "Alpaca 페이퍼 브로커로 체결하기"를 켜도 이 목록은 비어 있습니다.</p>';
  }
  if (!items?.length) return '<p class="lvdca-empty">아직 Alpaca 페이퍼 계좌에 낸 주문이 없습니다. "Alpaca 페이퍼 브로커로 체결하기"를 켜야 기록됩니다.</p>';
  const rows = items.map((item) => `
    <tr>
      <td>${escapeHtml(item.date)}</td>
      <td>${escapeHtml(item.ticker)}${item.is_reserve ? ' <small>(예비비)</small>' : ''}</td>
      <td class="${item.direction === 'buy' ? 'is-up' : 'is-down'}">${item.direction === 'buy' ? '매수' : '매도'}</td>
      <td>${escapeHtml(ALPACA_STATUS_LABEL[item.status] || item.status)}</td>
      <td>${num(item.quantity, 6)}주<small> / 요청 ${num(item.requested_quantity, 6)}주</small></td>
      <td>${item.fill_price ? `$${num(item.fill_price, 4)}` : '-'}</td>
      <td>${item.notional_krw ? money(item.notional_krw) : '-'}</td>
      <td>${escapeHtml(item.reason || '-')}</td>
    </tr>`).join('');
  return `<div class="lvdca-table-wrap"><table><thead><tr><th>일시(KST)</th><th>종목</th><th>구분</th><th>상태</th><th>체결 수량</th><th>체결가</th><th>금액</th><th>사유</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function positionsHtml(positions) {
  if (!positions?.length) return '<p class="lvdca-empty">현재 모의 보유 포지션이 없습니다.</p>';
  const rows = positions.map((item) => `
    <div class="lvdca-position"><span>${escapeHtml(item.ticker)}</span><strong>${num(item.quantity, 4)}주</strong><small>평균단가 ${money(item.average_cost_krw)}</small></div>`).join('');
  return `<div class="lvdca-position-grid">${rows}</div>`;
}

const usd = (value) => (value == null ? '-' : `$${Number(value).toFixed(2)}`);
const signedMoney = (value) => (value == null ? '-' : `${Number(value) >= 0 ? '+' : '-'}${money(Math.abs(value))}`);
const signedPct = (value) => (value == null ? '-' : `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)}%`);
const tone = (value) => (value == null ? '' : Number(value) >= 0 ? 'is-up' : 'is-down');

function holdingSummaryHtml(summary, emptyText) {
  if (!summary) return `<p class="lvdca-empty">${emptyText}</p>`;
  const rows = summary.tickers || [];
  const errors = summary.errors?.length ? `<p class="lvdca-note is-warn">${escapeHtml(summary.errors.join('; '))}</p>` : '';
  const notes = summary.notes?.length ? `<p class="lvdca-config-note">${escapeHtml(summary.notes.join('; '))}</p>` : '';
  if (!rows.length) return `${errors}<p class="lvdca-empty">${emptyText}</p>`;
  const totals = summary.totals || {}; const tax = summary.after_tax;
  // 사용자 승인(2026-10-27): 수익률은 평가손익과 같은 원화(환율 포함) 기준. 평균 매수 환율과 가격·환율 손익을 나눠 보여 준다.
  const tableRows = rows.map((row) => `<tr><td>${escapeHtml(row.ticker)}</td><td>${num(row.quantity, 4)}주</td><td>${usd(row.average_cost_usd)}<br><small>${money(row.average_cost_krw)}${row.average_fx_rate ? ` · 매수 환율 ${Number(row.average_fx_rate).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}원/$` : ''}</small></td><td>${usd(row.current_price_usd)}</td><td>${row.market_value_krw == null ? '-' : money(row.market_value_krw)}</td><td class="${tone(row.unrealized_pnl_krw)}">${signedMoney(row.unrealized_pnl_krw)}${row.fx_pnl_krw == null ? '' : `<br><small>가격 ${signedMoney(row.price_pnl_krw)} · 환율 ${signedMoney(row.fx_pnl_krw)}</small>`}</td><td class="${tone(row.return_pct)}">${signedPct(row.return_pct)}${row.return_usd_pct == null ? '' : `<br><small>달러 기준 ${signedPct(row.return_usd_pct)}</small>`}</td></tr>`).join('');
  return `
    <div class="lvdca-metric-grid">
      <article class="lvdca-metric"><span>누적 매수 원가</span><strong>${money(totals.cost_basis_krw)}</strong></article>
      <article class="lvdca-metric"><span>현재 평가금액</span><strong>${totals.market_value_krw == null ? '-' : money(totals.market_value_krw)}</strong></article>
      <article class="lvdca-metric ${tone(totals.return_pct)}"><span>현재 수익률 (평가손익)</span><strong>${signedPct(totals.return_pct)}</strong><small>${signedMoney(totals.unrealized_pnl_krw)}</small></article>
      <article class="lvdca-metric ${tone(tax?.net_profit_after_tax_krw)}"><span>세후 실순수익 (지금 전량 매도 가정)</span><strong>${tax ? signedMoney(tax.net_profit_after_tax_krw) : '-'}</strong><small>${tax ? `세후 ${signedPct(tax.net_return_pct)}` : ''}</small></article>
    </div>
    <div class="lvdca-table-wrap"><table><thead><tr><th>종목</th><th>수량</th><th>누적 평단</th><th>현재가</th><th>평가금액</th><th>평가손익(원)</th><th>수익률(원화, 환율 포함)</th></tr></thead><tbody>${tableRows}</tbody></table></div>
    ${tax ? `<p class="lvdca-config-note">세후 실순수익 = 평가손익 ${signedMoney(tax.gross_profit_krw)} − 매도수수료 ${money(tax.sell_fee_krw)} − 예상 양도세 ${money(tax.estimated_tax_krw)}. 과세 기준: ${escapeHtml(tax.tax_rule)}. 올해 실현손익 ${signedMoney(tax.realized_gain_ytd_krw)}, 남은 기본공제 ${money(tax.deduction_remaining_krw)}. 환율 ${summary.fx_rate ? Number(summary.fx_rate).toFixed(2) : '-'}원/$. 다른 해외주식 손익과 합산되는 실제 신고 세액과는 다를 수 있습니다.</p>` : ''}
    ${notes}${errors}`;
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
    <section class="lvdca-section" id="lvdca-holding-summary">
      <h3><i class="fa-solid fa-calculator"></i> 누적 평단·수익률 — 홈페이지 모의 계좌 <small>(주전략+예비비 합산)</small></h3>
      ${holdingSummaryHtml(status.holding_summary, '아직 보유 중인 모의 체결이 없습니다.')}
    </section>
    <section class="lvdca-section" id="lvdca-alpaca-holding-summary">
      <h3><i class="fa-solid fa-building-columns"></i> 누적 평단·수익률 — Alpaca 페이퍼 계좌 <small>(계좌 실제 보유)</small></h3>
      ${holdingSummaryHtml(status.alpaca_holding_summary, 'Alpaca 페이퍼 계좌가 연결되지 않았거나 보유가 없습니다.')}
    </section>
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

    <section class="risk-basis-note lvdca-readonly" id="lvdca-readonly" hidden>
      <i class="fa-solid fa-eye"></i>
      <p><b>모니터링 전용.</b> 이 계정은 운영자 계정의 레버리지 DCA 현황·포지션·체결 내역을 볼 수만 있습니다.
      설정 변경, 즉시 실행, 기록 초기화는 운영자 계정에서만 할 수 있습니다.</p>
    </section>

    <section class="risk-basis-note lvdca-warning" id="lvdca-change-lock">
      <i class="fa-solid fa-lock"></i>
      <p><b>변경 잠금.</b> 레버리지 DCA 전략(매수·매도 규칙, 실행 시각, 체결 방식, Alpaca 연동)은 <b>사용자 승인 없이 변경할 수 없습니다.</b>
      엔진·KQS 등 다른 작업 중에도 이 전략의 코드와 데이터는 사용자가 직접 요청·승인한 경우에만 수정합니다.</p>
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
        <label class="lvdca-toggle"><input type="checkbox" id="lvdca-alpaca-enabled" /> <span>Alpaca 페이퍼 브로커에도 동시에 체결하기</span></label>
        <p class="lvdca-config-note" id="lvdca-alpaca-note">켜면 <b>홈페이지 자체 모의 체결</b>과 <b>Alpaca 페이퍼 계좌 체결</b>이 서로 독립적으로 각각 실행됩니다. 두 엔진의 체결가·수량은 완전히 별개라 정확히 같지 않을 수 있습니다(아래 두 표에 각각 표시). 서버에 <code>ALPACA_PAPER_API_KEY</code>/<code>ALPACA_PAPER_API_SECRET</code>가 설정된 경우에만 Alpaca 쪽이 동작하며, 키가 없거나 Alpaca 쪽에서 오류가 나도 홈페이지 자체 체결에는 영향이 없습니다. 실거래 엔드포인트는 절대 호출하지 않습니다.</p>
        <button type="submit" class="run-btn" id="lvdca-save"><i class="fa-solid fa-floppy-disk"></i> 설정 저장</button>
        <span id="lvdca-config-status" class="lvdca-config-status"></span>
      </form>
    </section>

    <div id="lvdca-status-body"><p class="lvdca-empty">현황을 불러오는 중…</p></div>

    <section class="lvdca-section">
      <div class="lvdca-section-head">
        <h3><i class="fa-solid fa-list"></i> 매수·매도 내역</h3>
        <button type="button" class="lvdca-reset-btn" id="lvdca-reset"><i class="fa-solid fa-trash-can"></i> 테스트 기록 초기화</button>
      </div>
      <p class="lvdca-config-note">지금까지 "지금 실행"으로 테스트한 모의 체결·포지션·실행 기록을 모두 지웁니다(Alpaca 체결 기록 포함). 설정값은 유지되며, 실거래 계좌에는 영향이 없습니다.</p>
      <h4>홈페이지 자체 모의 체결</h4>
      <div id="lvdca-trades-body"><p class="lvdca-empty">내역을 불러오는 중…</p></div>
      <h4>Alpaca 페이퍼 체결</h4>
      <div id="lvdca-alpaca-trades-body"><p class="lvdca-empty">내역을 불러오는 중…</p></div>
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
  const alpacaTradesBody = container.querySelector('#lvdca-alpaca-trades-body');
  const runButton = container.querySelector('#lvdca-run-now');

  // 운영자가 아닌 계정은 모니터링만: 입력·저장·실행·초기화를 모두 막는다(서버도 403으로 거부한다).
  const applyReadOnly = (readOnly) => {
    container.querySelector('#lvdca-readonly').hidden = !readOnly;
    container.querySelectorAll('#lvdca-config-form input').forEach((input) => { input.disabled = readOnly; });
    ['#lvdca-save', '#lvdca-run-now', '#lvdca-reset'].forEach((selector) => {
      const button = container.querySelector(selector);
      if (button) { button.hidden = readOnly; button.disabled = readOnly; }
    });
    container.querySelector('.lvdca-config h2').innerHTML = readOnly
      ? '<i class="fa-solid fa-sliders"></i> 자동매매 설정 <small style="color:var(--text-muted);font-weight:500;">(운영자 설정 · 읽기 전용)</small>'
      : '<i class="fa-solid fa-sliders"></i> 자동매매 설정';
  };

  const loadConfig = async () => {
    try {
      const config = await authRequest('/api/strategy/leverage-dca/config');
      applyReadOnly(Boolean(config.read_only));
      enabledInput.checked = Boolean(config.enabled);
      dailyBuyInput.value = config.policy.daily_buy_krw;
      maxPositionInput.value = config.policy.max_position_krw;
      sellTargetInput.value = config.policy.sell_net_profit_target_krw;
      reserveEnabledInput.checked = Boolean(config.reserve_enabled);
      reserveCashInput.value = config.policy.reserve_cash_krw;
      alpacaEnabledInput.checked = Boolean(config.alpaca_enabled);
      alpacaNote.classList.toggle('is-warn', !config.alpaca_paper_configured);
      if (!config.alpaca_paper_configured) {
        alpacaNote.textContent = '서버에 Alpaca 페이퍼 API 키가 설정되지 않아, 켜도 Alpaca 쪽은 실행되지 않고 홈페이지 자체 체결만 정상 진행됩니다.';
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
      alpacaTradesBody.innerHTML = alpacaTradesHtml(trades.alpaca_items, trades.alpaca_paper_configured);
    } catch (error) {
      tradesBody.innerHTML = `<p class="lvdca-empty" style="color:var(--red)">${escapeHtml(error.message)}</p>`;
      alpacaTradesBody.innerHTML = '';
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

  const resetButton = container.querySelector('#lvdca-reset');
  resetButton.addEventListener('click', async () => {
    if (!window.confirm('테스트로 실행한 모의 체결·포지션·실행 기록을 모두 지울까요? 설정값은 유지됩니다.')) return;
    resetButton.disabled = true;
    resetButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 초기화 중…';
    try {
      await authRequest('/api/strategy/leverage-dca/trades', { method: 'DELETE' });
      await Promise.all([loadStatus(), loadTrades()]);
    } catch (error) {
      tradesBody.innerHTML = `<p class="lvdca-empty" style="color:var(--red)">${escapeHtml(error.message)}</p>`;
    } finally {
      resetButton.disabled = false;
      resetButton.innerHTML = '<i class="fa-solid fa-trash-can"></i> 테스트 기록 초기화';
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
