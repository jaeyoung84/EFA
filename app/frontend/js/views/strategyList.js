import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';

const TYPE_BADGE = {
  real_backtest: { label: '실제 가격', color: '#22c55e' },
  virtual_backtest: { label: '가상 시뮬레이션', color: '#f59e0b' },
  lean: { label: 'LEAN 사전 실행', color: '#f0b90b' },
};

const pct = (value, digits = 2) => (value == null ? '-' : `${(Number(value) * 100).toFixed(digits)}%`);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
const money = (value) => `₩${Math.round(Number(value) || 0).toLocaleString('ko-KR')}`;
const signedMoney = (value) => `${Number(value) >= 0 ? '+' : ''}${money(value)}`;

function badge(type) {
  const meta = TYPE_BADGE[type] || { label: type, color: '#94a3b8' };
  return `<span style="padding:3px 9px; border-radius:20px; font-size:.68rem; font-weight:700; background:${meta.color}22; color:${meta.color};">${meta.label}</span>`;
}

function strategyCard(item) {
  const m = item.snapshot?.metrics;
  return `<article data-strategy-id="${item.id}" style="cursor:pointer; padding:14px 16px; background:#12161f; border:1px solid #232938; border-radius:10px; margin-bottom:10px;">
    <div style="display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap;">
      <div>
        <div style="font-weight:700; color:var(--text); font-size:.9rem;">${escapeHtml(item.name)} <span style="color:#64748b; font-weight:500; font-size:.75rem;">v${item.version}</span></div>
        <div style="color:#94a3b8; font-size:.72rem; margin-top:3px;">${escapeHtml(item.ticker || item.symbol || '-')} · ${item.created_at ? item.created_at.slice(0, 10) : '-'}</div>
      </div>
      <div style="display:flex; align-items:center; gap:10px;">
        ${m ? `<span style="font-size:.78rem; color:${m.cagr >= 0 ? 'var(--up)' : 'var(--down)'};">CAGR ${pct(m.cagr, 1)}</span>` : ''}
        ${badge(item.type)}
      </div>
    </div>
  </article>`;
}

function metadataTable(item) {
  const p = item.params || {};
  let rows;
  if (item.type === 'real_backtest') {
    rows = [
      ['종목', item.ticker], ['기간', p.period], ['이동평균', `MA${p.fast_ma}/MA${p.slow_ma}`],
      ['거래비용', `${p.transaction_cost_bps ?? '-'}bp`], ['슬리피지', `${p.slippage_bps ?? '-'}bp`],
      ['벤치마크', p.benchmark_ticker || '-'], ['리밸런싱', item.rebalance_note], ['버전', `v${item.version}`],
    ];
  } else if (item.type === 'virtual_backtest') {
    rows = [
      ['데이터', 'GBM 가상 시뮬레이션(합성 가격)'], ['이동평균', `MA${p.fast_ma}/MA${p.slow_ma}`],
      ['거래일 수', p.n_days], ['변동성(σ)', p.volatility], ['리밸런싱', item.rebalance_note], ['버전', `v${item.version}`],
    ];
  } else {
    const snap = item.snapshot || {};
    rows = [
      ['종목', `${snap.label || ''} (${snap.code || item.symbol})`],
      ['기간', `${(snap.start_date || '').slice(0, 10)} ~ ${(snap.end_date || '').slice(0, 10)}`],
      ['상태', snap.status || '-'], ['리밸런싱', item.rebalance_note], ['버전', `v${item.version}`],
    ];
  }
  return `<table style="width:100%; border-collapse:collapse; font-size:.8rem; margin-bottom:16px;">
    <tbody>${rows.map(([k, v]) => `<tr><td style="padding:6px 10px; color:#64748b; width:120px;">${escapeHtml(k)}</td><td style="padding:6px 10px; color:var(--text);">${escapeHtml(v ?? '-')}</td></tr>`).join('')}</tbody>
  </table>`;
}

function proposalRow(p) {
  const statusColor = { pending: '#f59e0b', approved: '#22c55e', rejected: '#ef4444' }[p.status] || '#94a3b8';
  const statusLabel = { pending: '대기', approved: '승인됨', rejected: '거절됨' }[p.status] || p.status;
  return `<tr data-proposal-id="${p.id}">
    <td style="padding:8px 10px;">${p.direction === 'buy' ? '매수' : '매도'}</td>
    <td style="padding:8px 10px; color:#94a3b8; font-size:.72rem;">${escapeHtml(p.basis)}</td>
    <td style="padding:8px 10px;">${Number(p.reference_price).toLocaleString('ko-KR')}</td>
    <td style="padding:8px 10px; color:${statusColor};">${statusLabel}</td>
    <td style="padding:8px 10px;">
      ${p.status === 'pending' ? `
        <input type="number" min="0.000001" step="any" placeholder="수량" class="param-input" data-proposal-qty style="width:90px; display:inline-block;">
        <button class="run-btn" data-proposal-approve style="padding:6px 10px; font-size:.72rem;">승인</button>
        <button type="button" data-proposal-reject style="padding:6px 10px; font-size:.72rem; color:#ef4444; background:none; border:1px solid #ef4444; border-radius:6px;">거절</button>
      ` : '-'}
    </td>
  </tr>`;
}

function fillRow(f) {
  const quantity = f.requested_quantity != null && Number(f.requested_quantity) !== Number(f.quantity)
    ? `${f.requested_quantity} → <b title="위험 한도로 자동 감액된 수량">${f.quantity}</b>` : f.quantity;
  return `<tr>
    <td style="padding:8px 10px;">${f.filled_at ? f.filled_at.slice(0, 16).replace('T', ' ') : '-'}</td>
    <td style="padding:8px 10px;">${escapeHtml(f.ticker)}</td>
    <td style="padding:8px 10px;">${f.direction === 'buy' ? '매수' : '매도'}</td>
    <td style="padding:8px 10px;">${quantity}</td>
    <td style="padding:8px 10px;">${Number(f.fill_price).toLocaleString('ko-KR')}</td>
  </tr>`;
}

function paperDashboardHtml(data) {
  if (!data.fill_count) return `<div><h2 style="margin:0 0 5px; color:var(--text); font-size:1rem;">페이퍼 트레이딩 대시보드</h2><p style="margin:0; color:#94a3b8; font-size:.78rem;">아직 모의 체결이 없습니다. 실제 가격 전략의 주문 제안을 승인하면 현금, 포지션, 성과가 여기에 표시됩니다.</p></div>`;
  const tone = Number(data.net_pnl_krw) >= 0 ? 'var(--up)' : 'var(--down)';
  const curve = data.equity_curve || [];
  const values = curve.map((point) => Number(point.equity_krw)).filter(Number.isFinite);
  const min = Math.min(...values); const max = Math.max(...values); const span = max - min || 1;
  const points = values.map((value, index) => `${index * (100 / Math.max(values.length - 1, 1))},${92 - ((value - min) / span * 80)}`).join(' ');
  const positions = (data.positions || []).map((p) => `<tr><td>${escapeHtml(p.strategy_name)}</td><td>${escapeHtml(p.ticker)}</td><td>${p.quantity}</td><td>${money(p.market_value_krw)}</td><td style="color:${Number(p.unrealized_pnl_krw) >= 0 ? 'var(--up)' : 'var(--down)'}">${signedMoney(p.unrealized_pnl_krw)}</td></tr>`).join('');
  const strategies = (data.strategy_summary || []).map((s) => `<tr><td>${escapeHtml(s.strategy_name)}</td><td>${s.open_position_count}</td><td style="color:${Number(s.realized_pnl_krw) >= 0 ? 'var(--up)' : 'var(--down)'}">${signedMoney(s.realized_pnl_krw)}</td><td style="color:${Number(s.unrealized_pnl_krw) >= 0 ? 'var(--up)' : 'var(--down)'}">${s.unrealized_pnl_krw == null ? '-' : signedMoney(s.unrealized_pnl_krw)}</td><td>${s.net_pnl_krw == null ? '-' : signedMoney(s.net_pnl_krw)}</td></tr>`).join('');
  const unavailable = !data.available ? `<p style="margin:12px 0 0; color:#f59e0b; font-size:.72rem;">일부 현재 시세를 가져오지 못해 미실현손익·자산총액을 표시하지 않았습니다. ${data.unpriced.map((item) => escapeHtml(item.ticker)).join(', ')}</p>` : '';
  return `<header style="display:flex; justify-content:space-between; align-items:start; gap:12px; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:1rem;">페이퍼 트레이딩 대시보드</h2><p style="margin:0; color:#94a3b8; font-size:.72rem;">실제 보유자산과 분리된 모의 체결 원장입니다.</p></div><button type="button" id="stg-dashboard-refresh" style="padding:7px 10px; color:var(--text); border:1px solid #334155; border-radius:7px; font-size:.72rem;"><i class="fa-solid fa-rotate"></i> 현재 시세 갱신</button></header><div style="display:grid; grid-template-columns:repeat(5,minmax(120px,1fr)); gap:9px; margin-top:14px;"><div class="metric-box"><small>총 자산</small><strong>${data.equity_krw == null ? '-' : money(data.equity_krw)}</strong></div><div class="metric-box"><small>현금</small><strong>${money(data.cash_krw)}</strong></div><div class="metric-box"><small>실현 손익</small><strong style="color:${Number(data.realized_pnl_krw) >= 0 ? 'var(--up)' : 'var(--down)'}">${signedMoney(data.realized_pnl_krw)}</strong></div><div class="metric-box"><small>미실현 손익</small><strong style="color:${Number(data.unrealized_pnl_krw) >= 0 ? 'var(--up)' : 'var(--down)'}">${data.unrealized_pnl_krw == null ? '-' : signedMoney(data.unrealized_pnl_krw)}</strong></div><div class="metric-box"><small>누적 손익률</small><strong style="color:${tone}">${data.return_pct == null ? '-' : `${data.return_pct >= 0 ? '+' : ''}${data.return_pct.toFixed(2)}%`}</strong></div></div><div style="margin-top:14px; padding:10px; background:#0f172a; border:1px solid #232938; border-radius:8px;"><svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="모의 계좌 자산 곡선" style="display:block; width:100%; height:110px;"><polyline points="${points}" fill="none" stroke="#22c55e" stroke-width="2" vector-effect="non-scaling-stroke"/></svg><div style="display:flex; justify-content:space-between; color:#64748b; font-size:.65rem;"><span>${money(min)}</span><span>${money(max)}</span></div></div><p style="margin:8px 0 0; color:#64748b; font-size:.67rem;">${escapeHtml(data.equity_curve_basis)}</p>${unavailable}<div style="display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:14px;"><section style="overflow:auto;"><h3 style="margin:0 0 8px; color:var(--text); font-size:.8rem;">열린 모의 포지션</h3>${positions ? `<table style="width:100%; min-width:440px; border-collapse:collapse; font-size:.7rem;"><thead><tr style="color:#64748b; text-align:left;"><th>전략</th><th>종목</th><th>수량</th><th>평가액</th><th>미실현</th></tr></thead><tbody>${positions}</tbody></table>` : '<p style="color:#94a3b8; font-size:.72rem;">열린 포지션이 없습니다.</p>'}</section><section style="overflow:auto;"><h3 style="margin:0 0 8px; color:var(--text); font-size:.8rem;">전략별 성과</h3><table style="width:100%; min-width:410px; border-collapse:collapse; font-size:.7rem;"><thead><tr style="color:#64748b; text-align:left;"><th>전략</th><th>포지션</th><th>실현</th><th>미실현</th><th>합계</th></tr></thead><tbody>${strategies}</tbody></table></section></div>`;
}

function paperHistoryHtml(data) {
  const rows = (data.snapshots || []).filter((item) => item.available && item.equity_krw != null);
  const values = rows.map((item) => Number(item.equity_krw));
  if (!values.length) return `<header style="display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">일별 성과 기록</h2><p style="margin:0; color:#94a3b8; font-size:.72rem;">저장된 유효 일별 평가가 아직 없습니다.</p></div><button type="button" class="run-btn" id="stg-snapshot-save" style="padding:7px 10px; font-size:.72rem;">오늘 평가 저장</button></header>`;
  const min = Math.min(...values); const max = Math.max(...values); const span = max - min || 1;
  const points = values.map((value, index) => `${index * (100 / Math.max(values.length - 1, 1))},${92 - ((value - min) / span * 80)}`).join(' ');
  const metric = data.metrics || {};
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">일별 성과 기록</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">유효 스냅샷 ${data.valid_snapshot_count}개 · ${escapeHtml(data.method)}</p></div><div style="display:flex; gap:7px;"><select id="stg-history-period" class="param-input" style="width:auto; padding:7px;"><option value="1m" ${data.period === '1m' ? 'selected' : ''}>1개월</option><option value="3m" ${data.period === '3m' ? 'selected' : ''}>3개월</option><option value="6m" ${data.period === '6m' ? 'selected' : ''}>6개월</option><option value="1y" ${data.period === '1y' ? 'selected' : ''}>1년</option><option value="all" ${data.period === 'all' ? 'selected' : ''}>전체</option></select><button type="button" class="run-btn" id="stg-snapshot-save" style="padding:7px 10px; font-size:.72rem;">오늘 평가 저장</button></div></header><div style="display:grid; grid-template-columns:repeat(4,minmax(100px,1fr)); gap:8px; margin-top:12px;"><div class="metric-box"><small>기간 수익률</small><strong>${metric.period_return_pct == null ? '-' : `${metric.period_return_pct >= 0 ? '+' : ''}${metric.period_return_pct.toFixed(2)}%`}</strong></div><div class="metric-box"><small>MDD</small><strong>${metric.mdd_pct == null ? '-' : `${metric.mdd_pct.toFixed(2)}%`}</strong></div><div class="metric-box"><small>연환산 변동성</small><strong>${metric.annual_volatility_pct == null ? '-' : `${metric.annual_volatility_pct.toFixed(2)}%`}</strong></div><div class="metric-box"><small>Sharpe</small><strong>${metric.sharpe == null ? '-' : metric.sharpe.toFixed(2)}</strong></div></div><div style="margin-top:10px; padding:8px; background:#0f172a; border:1px solid #232938; border-radius:8px;"><svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="일별 모의 계좌 자산 곡선" style="display:block; width:100%; height:100px;"><polyline points="${points}" fill="none" stroke="#38bdf8" stroke-width="2" vector-effect="non-scaling-stroke"/></svg><div style="display:flex; justify-content:space-between; color:#64748b; font-size:.65rem;"><span>${rows[0].snapshot_date}</span><span>${rows.at(-1).snapshot_date}</span></div></div>`;
}

function paperAlertsHtml(items, history) {
  const label = { open: '새 알림', acknowledged: '확인함', deferred: '보류', resolved: '해결됨' };
  const severity = { critical: '#ef4444', warning: '#f59e0b', info: '#38bdf8' };
  const rows = items.map((item) => `<article data-paper-alert="${item.id}" style="padding:10px 0; border-top:1px solid #232938;"><div style="display:flex; justify-content:space-between; gap:10px; align-items:start;"><div><b style="color:${severity[item.severity] || '#94a3b8'}; font-size:.78rem;">${escapeHtml(item.title)}</b><p style="margin:5px 0; color:var(--text); font-size:.76rem;">${escapeHtml(item.message)}</p><small style="color:#64748b;">${label[item.status] || item.status} · ${item.updated_at ? item.updated_at.slice(0, 16).replace('T', ' ') : '-'}</small>${history && item.events?.length ? `<details style="margin-top:6px;"><summary style="color:#94a3b8; font-size:.68rem; cursor:pointer;">처리 이력 ${item.events.length}건</summary><ul style="margin:5px 0 0; padding-left:16px; color:#94a3b8; font-size:.67rem;">${item.events.map((event) => `<li>${escapeHtml(event.action)} · ${escapeHtml(event.note || '-')} · ${event.at ? event.at.slice(0, 16).replace('T', ' ') : '-'}</li>`).join('')}</ul></details>` : ''}</div>${!history && item.status !== 'resolved' ? `<div style="display:flex; flex-direction:column; gap:5px;"><button type="button" data-alert-action="acknowledged" style="font-size:.67rem; color:#38bdf8; border:1px solid #38bdf8; border-radius:5px; padding:4px 6px;">확인</button><button type="button" data-alert-action="deferred" style="font-size:.67rem; color:#f59e0b; border:1px solid #f59e0b; border-radius:5px; padding:4px 6px;">보류</button><button type="button" data-alert-action="resolved" style="font-size:.67rem; color:#22c55e; border:1px solid #22c55e; border-radius:5px; padding:4px 6px;">해결</button></div>` : ''}</div></article>`).join('');
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">리밸런싱 알림함</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">위험 한도, 일별 손실, 대기 중인 전략 주문 신호를 통합합니다. 확인은 주문 실행이 아닙니다.</p></div><div style="display:flex; gap:6px;"><button type="button" id="stg-alert-refresh" style="padding:6px 8px; border:1px solid #334155; border-radius:6px; color:var(--text); font-size:.7rem;">새로고침</button><button type="button" id="stg-alert-history" style="padding:6px 8px; border:1px solid #334155; border-radius:6px; color:var(--text); font-size:.7rem;">${history ? '활성 알림' : '처리 이력'}</button></div></header>${rows || `<p style="margin:12px 0 0; color:#94a3b8; font-size:.75rem;">${history ? '아직 알림 이력이 없습니다.' : '현재 활성 알림이 없습니다.'}</p>`}`;
}

function paperReportHtml(data) {
  const metrics = data.performance?.history || {};
  const audit = data.audit || {};
  const risk = data.risk || {};
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">감사·리서치 리포트</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">${escapeHtml(data.disclaimer)}</p></div><div style="display:flex; gap:6px; flex-wrap:wrap;"><select id="stg-report-period" class="param-input" style="width:auto; padding:7px;"><option value="1m" ${data.period === '1m' ? 'selected' : ''}>1개월</option><option value="3m" ${data.period === '3m' ? 'selected' : ''}>3개월</option><option value="6m" ${data.period === '6m' ? 'selected' : ''}>6개월</option><option value="1y" ${data.period === '1y' ? 'selected' : ''}>1년</option><option value="all" ${data.period === 'all' ? 'selected' : ''}>전체</option></select><button type="button" data-report-export="csv" style="padding:7px 9px; border:1px solid #22c55e; border-radius:6px; color:#22c55e; font-size:.7rem;">CSV</button><button type="button" data-report-export="json" style="padding:7px 9px; border:1px solid #38bdf8; border-radius:6px; color:#38bdf8; font-size:.7rem;">JSON</button></div></header><div style="display:grid; grid-template-columns:repeat(5,minmax(100px,1fr)); gap:8px; margin-top:12px;"><div class="metric-box"><small>기간 수익률</small><strong>${metrics.period_return_pct == null ? '-' : `${metrics.period_return_pct >= 0 ? '+' : ''}${metrics.period_return_pct.toFixed(2)}%`}</strong></div><div class="metric-box"><small>MDD</small><strong>${metrics.mdd_pct == null ? '-' : `${metrics.mdd_pct.toFixed(2)}%`}</strong></div><div class="metric-box"><small>Sharpe</small><strong>${metrics.sharpe == null ? '-' : metrics.sharpe.toFixed(2)}</strong></div><div class="metric-box"><small>체결 기록</small><strong>${audit.fill_count ?? 0}건</strong></div><div class="metric-box"><small>활성 위험 알림</small><strong>${risk.active_alert_count ?? 0}건</strong></div></div><p style="margin:10px 0 0; color:#64748b; font-size:.68rem;">생성 ${data.generated_at ? data.generated_at.slice(0, 16).replace('T', ' ') : '-'} · 유효 일별 스냅샷 ${data.performance?.valid_snapshot_count ?? 0}개 · 알림 이력 ${audit.alert_count ?? 0}건</p>`;
}

function executionStatusHtml(data, audit = null) {
  const permission = data.user_permission || {};
  const liveState = data.live_order_submission_available ? '활성' : '차단됨';
  const sandbox = data.sandbox_order_submission_available ? '샌드박스 준비됨(외부 전송 없음)' : '샌드박스 비활성';
  const auditLabel = audit?.verification?.valid ? `감사 원장 정상 · ${audit.verification.event_count}건` : '감사 원장 확인 불가';
  return `<div style="display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.86rem;">주문 실행 안전 상태</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">모의 주문 ${permission.paper_trading_enabled ? '<b style="color:#22c55e">허용</b>' : '<b style="color:#ef4444">중지</b>'} · 전역 킬스위치 ${data.global_kill_switch ? '<b style="color:#ef4444">활성</b>' : '해제'} · 실거래 ${liveState}</p><small style="color:#64748b; font-size:.64rem;">${sandbox} · ${auditLabel}</small><br><small style="color:#64748b; font-size:.64rem;">현재 배포본에는 실거래 어댑터가 없어 실주문은 전송되지 않습니다.</small></div><button type="button" id="stg-paper-permission" style="padding:7px 9px; border:1px solid ${permission.paper_trading_enabled ? '#f59e0b' : '#22c55e'}; border-radius:6px; color:${permission.paper_trading_enabled ? '#f59e0b' : '#22c55e'}; font-size:.7rem;">모의 주문 ${permission.paper_trading_enabled ? '중지' : '허용'}</button></div>`;
}

function strategyPortfolioHtml(data) {
  if (!data.available) return `<h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">전략 포트폴리오 배분안</h2><p style="margin:0; color:#94a3b8; font-size:.72rem;">${escapeHtml(data.reason)}</p>`;
  const rows = data.items.map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.ticker)}</td><td>${item.risk_budget_pct.toFixed(2)}%</td><td>${item.sharpe ?? '-'}</td><td>${item.mdd_pct.toFixed(2)}%</td><td>${item.benchmark_beta ?? '-'}</td></tr>`).join('');
  const parity = (data.risk_parity_items || []).map((item) => `${escapeHtml(item.name)} ${item.risk_budget_pct.toFixed(2)}%`).join(' · ');
  const correlations = (data.strategy_return_correlations || []).map((item) => `${item.correlation} (${item.observations}일)`).join(' / ');
  return `<h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">전략 포트폴리오 배분안</h2><p style="margin:0 0 10px; color:#94a3b8; font-size:.7rem;">${escapeHtml(data.method)}</p><div style="overflow:auto;"><table style="width:100%; min-width:580px; border-collapse:collapse; font-size:.72rem;"><thead><tr style="color:#64748b; text-align:left;"><th>전략</th><th>종목</th><th>위험 예산</th><th>Sharpe</th><th>MDD</th><th>베타</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div><small style="display:block; margin-top:8px; color:#64748b;">집중도 HHI ${data.concentration_hhi} · ${escapeHtml(data.strategy_return_correlation)}</small>${parity ? `<small style="display:block; margin-top:4px; color:#38bdf8;">역변동성 리스크 패리티 참고안: ${parity}</small>` : ''}${correlations ? `<small style="display:block; margin-top:4px; color:#94a3b8;">전략 간 상관계수: ${correlations}</small>` : ''}`;
}

function rebalancePlanHtml(data) {
  if (!data.available) return `<h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">전략 리밸런싱 계획</h2><p style="margin:0; color:#94a3b8; font-size:.72rem;">${escapeHtml(data.reason)}</p>`;
  const label = { increase_review: '증액 검토', decrease_review: '감액 검토', within_tolerance: '유지' };
  const rows = data.items.map((item) => `<tr><td>${escapeHtml(item.name)}</td><td>${item.current_weight_pct.toFixed(2)}%</td><td>${item.target_risk_budget_pct.toFixed(2)}%</td><td>${signedMoney(item.delta_value_krw)}</td><td>${label[item.action] || item.action}</td></tr>`).join('');
  return `<h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">전략 리밸런싱 계획</h2><p style="margin:0 0 8px; color:#94a3b8; font-size:.7rem;">${escapeHtml(data.method)}</p><div style="overflow:auto;"><table style="width:100%; min-width:500px; border-collapse:collapse; font-size:.72rem;"><thead><tr style="color:#64748b; text-align:left;"><th>전략</th><th>현재</th><th>목표</th><th>차이(KRW)</th><th>검토</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div><small style="display:block; margin-top:7px; color:#64748b;">허용 오차 ±${data.tolerance_pct}% · 현재 모의 자산 ${money(data.equity_krw)}</small>`;
}

function versionComparisonHtml(data) {
  if (!data.available) return `<p style="margin:0; color:#94a3b8; font-size:.72rem;">${escapeHtml(data.reason)}</p>`;
  const delta = data.metric_changes || {};
  const assessment = data.promotion_assessment || {};
  const impact = data.allocation_impact || {};
  return `<div style="margin:0 0 14px; padding:10px; background:#0f172a; border:1px solid #334155; border-radius:8px;"><b style="font-size:.76rem; color:${assessment.status === 'candidate' ? '#22c55e' : '#f59e0b'};">버전 승격 ${assessment.status === 'candidate' ? '후보' : '검토 필요'}</b><p style="margin:5px 0; color:#94a3b8; font-size:.7rem;">${escapeHtml(assessment.reason || '')} · 자동 승격 없음</p><small style="color:#cbd5e1; font-size:.68rem;">CAGR Δ ${(delta.cagr * 100).toFixed(2)}% · Sharpe Δ ${delta.sharpe.toFixed(2)} · MDD Δ ${(delta.mdd * 100).toFixed(2)}% · 수익률 상관 ${data.return_correlation ?? '-'} · 위험예산 Δ ${impact.delta_risk_budget_pct ?? '-'}%</small></div>`;
}

function versionPromotionHtml(data) {
  const history = (data.history || []).slice(0, 3).map((event) => `${event.action === 'approved' ? '승인' : '철회'} v${event.strategy_version} · ${event.created_at ? event.created_at.slice(0, 16).replace('T', ' ') : '-'}${event.note ? ` · ${escapeHtml(event.note)}` : ''}`).join('<br>');
  const operatorHint = data.can_manage ? '' : '<small style="display:block; margin-top:6px; color:#64748b;">승인·철회는 서버의 EXECUTION_OPERATOR_EMAILS에 등록된 운영자 계정에서만 할 수 있습니다.</small>';
  if (data.is_current_approved) {
    return `<div style="margin:-6px 0 14px; padding:10px; background:#102a1f; border:1px solid #22c55e; border-radius:8px;"><b style="font-size:.76rem; color:#22c55e;">운영자 승인 버전 고정됨</b><p style="margin:5px 0; color:#cbd5e1; font-size:.7rem;">전략 정의 지문 ${escapeHtml((data.history?.[0]?.strategy_fingerprint || '').slice(0, 12))}… · 주문·배분은 바뀌지 않습니다.</p>${data.approval_fingerprint_matches === false ? '<p style="margin:5px 0; color:#ef4444; font-size:.7rem;">승인 당시 지문과 현재 전략 내용이 일치하지 않습니다. 검토가 필요합니다.</p>' : ''}${data.can_manage ? '<button type="button" data-promotion-revoke style="padding:6px 9px; color:#f59e0b; border:1px solid #f59e0b; border-radius:6px; font-size:.7rem;">승인 철회</button>' : ''}${history ? `<small style="display:block; margin-top:8px; color:#94a3b8; font-size:.65rem;">${history}</small>` : ''}${operatorHint}</div>`;
  }
  const action = data.eligible_for_approval && data.can_manage
    ? '<button type="button" class="run-btn" data-promotion-approve style="padding:6px 9px; font-size:.7rem;">이 버전 승인·고정</button>'
    : '<small style="color:#64748b; font-size:.68rem;">승인 전에는 비교 결과·운영자 권한을 확인하세요.</small>';
  return `<div style="margin:-6px 0 14px; padding:10px; background:#0f172a; border:1px solid #334155; border-radius:8px;"><b style="font-size:.76rem; color:#cbd5e1;">운영자 버전 승인</b><p style="margin:5px 0; color:#94a3b8; font-size:.7rem;">승인하면 이 전략 계보의 선택 버전만 고정합니다. 주문 생성·모의 체결·배분 변경은 하지 않습니다.</p>${action}${history ? `<small style="display:block; margin-top:8px; color:#64748b; font-size:.65rem;">${history}</small>` : ''}${operatorHint}</div>`;
}

function promotionMonitorHtml(data) {
  const rows = data.records || [];
  if (!rows.length) return `<h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">승인 전략 모의운용 점검</h2><p style="margin:0; color:#94a3b8; font-size:.72rem;">현재 승인 고정된 전략이 없습니다.</p>`;
  const status = { monitoring: '모니터링', waiting_for_paper_fills: '모의 체결 대기', strategy_missing: '전략 확인 필요' };
  const table = rows.map((item) => `<tr><td>${escapeHtml(item.strategy_name)} v${item.strategy_version}</td><td>${status[item.status] || item.status}</td><td>${item.approval_age_days}일${item.review_due ? ' · 재검토' : ''}</td><td>${item.paper_return_proxy_pct == null ? '-' : `${item.paper_return_proxy_pct.toFixed(2)}%`}</td></tr>`).join('');
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">승인 전략 모의운용 점검</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">승인 후 ${data.review_period_days}일마다 재검토합니다. 성과는 누적 체결원가 기준 참고값이며 자동 철회·주문은 하지 않습니다.</p></div><button type="button" id="stg-promotion-monitor-refresh" style="padding:7px 9px; border:1px solid #38bdf8; border-radius:6px; color:#38bdf8; font-size:.7rem;">지금 점검</button></header><div style="overflow:auto; margin-top:10px;"><table style="width:100%; min-width:460px; border-collapse:collapse; font-size:.7rem;"><thead><tr style="text-align:left; color:#64748b;"><th>전략</th><th>상태</th><th>승인 경과</th><th>참고 성과</th></tr></thead><tbody style="color:var(--text);">${table}</tbody></table></div><small style="display:block; margin-top:7px; color:${data.findings?.length ? '#f59e0b' : '#64748b'};">${data.findings?.length ? `재검토 알림 ${data.findings.length}건` : '현재 재검토 알림 없음'}</small>`;
}

function liveReviewReadinessHtml(data) {
  const policy = data.policy || {};
  const label = { monitoring: '모의운용 진행', review_required: '재검토 필요', live_review_candidate: '실거래 검토 후보' };
  const color = { monitoring: '#38bdf8', review_required: '#f59e0b', live_review_candidate: '#22c55e' };
  const rows = (data.items || []).map((item) => {
    const metric = item.metrics || {};
    const reason = item.reasons?.[0] || '-';
    return `<tr><td>${escapeHtml(item.strategy_name)} v${item.strategy_version}</td><td style="color:${color[item.status] || '#94a3b8'};">${label[item.status] || item.status}</td><td>${metric.observed_trading_days ?? 0}/${policy.min_trading_days ?? '-'}일</td><td>${metric.mdd_pct == null ? '-' : `${metric.mdd_pct.toFixed(2)}%`}</td><td>${metric.performance_gap_pct == null ? '-' : `${metric.performance_gap_pct.toFixed(2)}%p`}</td><td>${escapeHtml(reason)}</td></tr>`;
  }).join('');
  if (!rows) return `<h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">장기 모의운용 적합성</h2><p style="margin:0; color:#94a3b8; font-size:.72rem;">승인 고정된 전략이 없습니다.</p>`;
  return `<h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">장기 모의운용 적합성</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">최소 ${policy.min_trading_days}개 모의운용 스냅샷과 주문 결정 ${policy.min_order_decisions}건을 확인합니다. 후보 표시는 실거래 권한·주문 전송을 만들지 않습니다.</p><div style="overflow:auto; margin-top:10px;"><table style="width:100%; min-width:690px; border-collapse:collapse; font-size:.68rem;"><thead><tr style="text-align:left; color:#64748b;"><th>전략</th><th>판정</th><th>관측</th><th>MDD</th><th>성과 괴리</th><th>사유</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div><small style="display:block; margin-top:7px; color:#64748b; font-size:.65rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function paperOrderPreflightHtml(data) {
  const messages = (data.reasons || []).map((item) => escapeHtml(item.message)).join(' · ');
  const alerts = (data.active_promotion_alerts || []).map((item) => escapeHtml(item.title)).join(' · ');
  const color = data.allowed ? '#22c55e' : '#ef4444';
  return `<div style="margin:0 0 10px; padding:9px; background:#0f172a; border:1px solid ${color}; border-radius:7px;"><b style="font-size:.72rem; color:${color};">모의 주문 사전 점검: ${data.allowed ? '통과' : '차단'}</b><p style="margin:4px 0; color:#94a3b8; font-size:.68rem;">${data.allowed ? escapeHtml(data.risk_quantity_check) : messages}</p>${alerts ? `<small style="color:#f59e0b; font-size:.65rem;">활성 재검토 경고: ${alerts}</small>` : ''}<small style="display:block; margin-top:4px; color:#64748b; font-size:.64rem;">승인 전략 제한 ${data.promotion_approval_required ? '활성' : '비활성'} · 실거래 권한 없음</small></div>`;
}

function operationsReportHtml(data) {
  const checks = (data.safety_checks || []).map((item) => `<li style="color:${item.passed ? '#22c55e' : '#ef4444'};">${item.passed ? '정상' : '점검 필요'} · ${escapeHtml(item.name)} <span style="color:#64748b;">${escapeHtml(item.detail)}</span></li>`).join('');
  const quality = data.order_quality || {};
  const market = data.market_data || {};
  const latest = market.latest_job || {};
  const marketSummary = market.summary || {};
  const marketAlerts = (market.alerts || []).filter((alert) => alert.condition_active).slice(0, 5);
  const providerHealth = market.provider_health || {};
  const providerLatencyTrends = Object.fromEntries((market.provider_latency_trends?.providers || []).map((provider) => [provider.provider, provider]));
  const routingByProvider = Object.fromEntries((market.provider_routing?.providers || []).map((provider) => [provider.provider, provider]));
  const providerRows = (providerHealth.providers || []).slice(0, 5).map((provider) => { const routing = routingByProvider[provider.provider]; const trend = providerLatencyTrends[provider.provider]; const slow = provider.performance_status === 'slow'; const regressed = trend?.status === 'regressed'; const latency = provider.average_latency_ms == null ? '측정 중' : `평균 ${provider.average_latency_ms}ms · P95 ${provider.p95_latency_ms}ms`; const trendLabel = regressed ? ` · 기준선 대비 +${trend.change_pct}%` : trend?.status === 'improved' ? ` · 기준선 대비 ${trend.change_pct}%` : ''; return `<li style="color:${provider.status === 'degraded' || routing?.cooling_down || slow || regressed ? '#f59e0b' : '#94a3b8'};">${escapeHtml(provider.provider)} · ${routing?.cooling_down ? '재시도 대기' : provider.status === 'degraded' ? '점검 필요' : regressed ? '응답 악화' : slow ? '응답 지연' : '정상'} · 시도 ${provider.attempt_count} · 오류 ${provider.error_rate_pct}% · 거부 ${provider.rejection_rate_pct}% · ${latency}${trendLabel}${provider.not_applicable_count ? ` · 비적용 ${provider.not_applicable_count}` : ''}</li>`; }).join('');
  const marketDetail = market.available === false ? (market.disclaimer || '확인 불가') : latest.status
    ? `최근 ${latest.target_count || 0}개 · 성공 ${latest.success_count || 0} / 실패 ${latest.failure_count || 0}`
    : '아직 자동 수집 이력이 없습니다';
  const marketAlertRows = marketAlerts.length ? `<ul style="margin:6px 0 0; padding-left:16px; color:#f59e0b; font-size:.66rem; line-height:1.7;">${marketAlerts.map((alert) => `<li>${escapeHtml(alert.title)} · ${escapeHtml(alert.context?.ticker || '-')} ${alert.context?.quality?.rejected ? `(거부 봉 ${alert.context.quality.rejected}개)` : ''}</li>`).join('')}</ul>` : '';
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">주간 운영 리포트</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">최근 ${data.period_days}일 · ${escapeHtml(data.disclaimer || '')}</p></div><span style="font-size:.7rem; color:#38bdf8;">${escapeHtml(data.execution_mode || 'paper')} 모드</span></header><ul style="margin:10px 0; padding-left:17px; font-size:.68rem; line-height:1.8;">${checks}</ul><div style="display:grid; grid-template-columns:repeat(6,minmax(90px,1fr)); gap:7px;"><div class="metric-box"><small>모의 체결</small><strong>${quality.fill_count ?? 0}</strong></div><div class="metric-box"><small>결정 승인율</small><strong>${quality.approval_rate_pct == null ? '-' : `${quality.approval_rate_pct}%`}</strong></div><div class="metric-box"><small>활성 경고</small><strong>${data.risk_and_review?.active_alert_count ?? 0}</strong></div><div class="metric-box"><small>장기 후보</small><strong>${data.risk_and_review?.live_review_candidate_count ?? 0}</strong></div><div class="metric-box"><small>감사 체인</small><strong>${data.audit?.verification?.valid ? '정상' : '확인 필요'}</strong></div><div class="metric-box"><small>시세 품질 경고</small><strong style="color:${marketSummary.active_alert_count ? '#f59e0b' : 'var(--up)'};">${marketSummary.active_alert_count ?? '-'}</strong></div></div><p style="margin:9px 0 0; color:${marketSummary.active_alert_count || marketSummary.stale_core_tickers?.length ? '#f59e0b' : '#64748b'}; font-size:.67rem;">시세 데이터 · ${escapeHtml(marketDetail)}${marketSummary.stale_core_tickers?.length ? ` · 최신성 확인 ${escapeHtml(marketSummary.stale_core_tickers.join(', '))}` : ''}</p>${marketAlertRows}${providerRows ? `<ul style="margin:6px 0 0; padding-left:16px; font-size:.65rem; line-height:1.65;">${providerRows}</ul>` : ''}<small style="display:block; margin-top:8px; color:#64748b; font-size:.65rem;">백업·복구 절차: ${escapeHtml(data.backup_restore?.runbook || '-')} · 자동 복원 없음</small>`;
}

function kqsStatusHtml(data) {
  if (!data.available) return `<h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">외부 퀀트 엔진 상태</h2><p style="margin:0; color:#f59e0b; font-size:.72rem;">${escapeHtml(data.detail || '엔진 브리지 상태를 확인하지 못했습니다.')}</p>`;
  const scheduler = data.scheduler || {}; const pipeline = data.pipeline || {}; const market = data.market_data || {}; const paper = data.paper || {}; const hashlock = data.hashlock || {};
  const forward = Object.entries(data.forward || {}).map(([name, judge]) => `${name.toUpperCase()} ${escapeHtml(judge?.verdict || '관측 정보 없음')}`).join(' · ');
  const tone = scheduler.last_result_ok && pipeline.status === 'ok' && Number(hashlock.mismatch || 0) === 0 ? 'var(--up)' : '#f59e0b';
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">외부 퀀트 엔진 상태</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">Ubuntu-26.04 엔진의 읽기 전용 운영 요약</p></div><span style="color:${tone}; font-size:.7rem;">${data.read_only ? '읽기 전용 연결' : '상태 확인 필요'}</span></header><div style="display:grid; grid-template-columns:repeat(4,minmax(100px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>DB 기준일</small><strong>${escapeHtml(market.latest_trade_date || '-')}</strong></div><div class="metric-box"><small>파이프라인</small><strong style="color:${pipeline.status === 'ok' ? 'var(--up)' : '#f59e0b'};">${escapeHtml(pipeline.status || '-')}</strong></div><div class="metric-box"><small>모의 포지션</small><strong>${paper.open_positions ?? '-'}</strong></div><div class="metric-box"><small>해시 잠금</small><strong style="color:${Number(hashlock.mismatch || 0) ? '#ef4444' : 'var(--up)'};">불일치 ${hashlock.mismatch ?? '-'}</strong></div></div><p style="margin:9px 0 0; color:#64748b; font-size:.67rem;">최근 실행 ${escapeHtml(scheduler.last_run || '-')} · 다음 실행 ${escapeHtml(scheduler.next_run || '-')} · ${escapeHtml(forward || '전향 관측 정보 없음')}</p><small style="display:block; margin-top:6px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsReviewCandidatesHtml(data) {
  if (!data.available) return `<h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">엔진 신규 후보 검토 큐</h2><p style="margin:0; color:#f59e0b; font-size:.72rem;">${escapeHtml(data.detail || '엔진 후보를 확인하지 못했습니다.')}</p>`;
  const reviewStatus = (review) => !review ? '<span style="color:#94a3b8;">미검토</span>' : `<span title="${escapeHtml(review.note || '')}" style="color:${review.status === 'reviewed' ? 'var(--up)' : '#f59e0b'};">${review.status === 'reviewed' ? '검토 완료' : '보류'}${review.note ? ' · 메모 있음' : ''}</span>`;
  const rows = (data.candidates || []).map((item) => `<tr><td style="padding:7px 8px;">${escapeHtml(item.name || item.symbol)}</td><td style="padding:7px 8px;">${escapeHtml(item.symbol)}</td><td style="padding:7px 8px;">${escapeHtml(item.market || '-')}</td><td style="padding:7px 8px;">${escapeHtml(item.signal_type || '-')}</td><td style="padding:7px 8px;">${escapeHtml(item.market_cap_rank ?? '-')}</td><td style="padding:7px 8px;">${reviewStatus(item.review)}</td><td style="padding:7px 8px; white-space:nowrap;"><button type="button" data-kqs-packet data-kqs-symbol="${escapeHtml(item.symbol)}" style="font-size:.66rem; padding:4px 6px;">근거 보기</button> <button type="button" data-kqs-review-status="reviewed" data-kqs-symbol="${escapeHtml(item.symbol)}" style="font-size:.66rem; padding:4px 6px;">검토 완료</button> <button type="button" data-kqs-review-status="deferred" data-kqs-symbol="${escapeHtml(item.symbol)}" style="font-size:.66rem; padding:4px 6px;">보류</button></td></tr>`).join('');
  const review = data.review_summary || {};
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">엔진 신규 후보 검토 큐</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">엔진 기준일 ${escapeHtml(data.as_of || '-')} · 다음 거래일 진입 대상만 표시</p></div><button type="button" id="stg-kqs-candidates-refresh" style="padding:7px 10px; color:var(--text); border:1px solid #334155; border-radius:7px; font-size:.72rem;"><i class="fa-solid fa-rotate"></i> 새로 고침</button></header><div style="display:grid; grid-template-columns:repeat(6,minmax(90px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>선택 후보</small><strong>${data.latest_selected_count ?? '-'}</strong></div><div class="metric-box"><small>다음 거래일 진입</small><strong style="color:${Number(data.entry_due_count || 0) ? 'var(--up)' : '#94a3b8'};">${data.entry_due_count ?? '-'}</strong></div><div class="metric-box"><small>레짐 차단</small><strong style="color:${Number(data.blocked_signal_count || 0) ? '#f59e0b' : 'var(--up)'};">${data.blocked_signal_count ?? '-'}</strong></div><div class="metric-box"><small>검토 완료</small><strong style="color:var(--up);">${review.reviewed_count ?? 0}</strong></div><div class="metric-box"><small>보류</small><strong style="color:#f59e0b;">${review.deferred_count ?? 0}</strong></div><div class="metric-box"><small>미검토</small><strong>${review.pending_count ?? 0}</strong></div></div>${rows ? `<div style="overflow-x:auto; margin-top:12px;"><table style="width:100%; min-width:720px; border-collapse:collapse; font-size:.7rem;"><thead><tr style="color:#64748b; text-align:left;"><th style="padding:7px 8px;">종목</th><th style="padding:7px 8px;">코드</th><th style="padding:7px 8px;">시장</th><th style="padding:7px 8px;">신호</th><th style="padding:7px 8px;">시총 순위</th><th style="padding:7px 8px;">검토</th><th style="padding:7px 8px;">기록</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div>` : '<p style="margin:12px 0 0; color:#94a3b8; font-size:.72rem;">최신 기준일에 검토할 신규 진입 후보가 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsCandidateReviewHistoryHtml(data) {
  const rows = (data.items || []).slice(0, 10).map((item) => `<tr><td style="padding:6px 8px;">${escapeHtml(item.as_of)}</td><td style="padding:6px 8px;">${escapeHtml(item.name || item.symbol)}</td><td style="padding:6px 8px;">${escapeHtml(item.symbol)}</td><td style="padding:6px 8px; color:${item.status === 'reviewed' ? 'var(--up)' : '#f59e0b'};">${item.status === 'reviewed' ? '검토 완료' : '보류'}</td><td style="padding:6px 8px;">${escapeHtml(item.note || '-')}</td></tr>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">후보 검토 감사 이력</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">최근 ${data.days}일 · 사용자별 메모 기록</p></header>${rows ? `<div style="overflow-x:auto; margin-top:10px;"><table style="width:100%; min-width:580px; border-collapse:collapse; font-size:.7rem;"><thead><tr style="color:#64748b; text-align:left;"><th style="padding:6px 8px;">기준일</th><th style="padding:6px 8px;">종목</th><th style="padding:6px 8px;">코드</th><th style="padding:6px 8px;">상태</th><th style="padding:6px 8px;">메모</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div>` : '<p style="margin:10px 0 0; color:#64748b; font-size:.72rem;">아직 저장된 후보 검토 기록이 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsCandidateReviewSnapshotHistoryHtml(data) {
  const rows = (data.items || []).slice(0, 10).map((item, index) => `<tr><td style="padding:6px 8px;">${escapeHtml(item.as_of)}</td><td style="padding:6px 8px;">${escapeHtml(item.name || item.symbol)}</td><td style="padding:6px 8px; color:${item.status === 'reviewed' ? 'var(--up)' : '#f59e0b'};">${item.status === 'reviewed' ? '검토 완료' : '보류'}</td><td style="padding:6px 8px;">${item.captured_at ? escapeHtml(item.captured_at.slice(0, 16).replace('T', ' ')) : '-'}</td><td style="padding:6px 8px;"><button type="button" data-kqs-archived-packet="${index}" style="font-size:.66rem; padding:4px 6px;">당시 패킷</button></td></tr>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">검토 시점 패킷 보관함</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">후보가 최신 큐에서 사라져도 당시 근거를 보존합니다.</p></header>${rows ? `<div style="overflow-x:auto; margin-top:10px;"><table style="width:100%; min-width:540px; border-collapse:collapse; font-size:.7rem;"><thead><tr style="color:#64748b; text-align:left;"><th style="padding:6px 8px;">기준일</th><th style="padding:6px 8px;">종목</th><th style="padding:6px 8px;">상태</th><th style="padding:6px 8px;">저장 시각</th><th style="padding:6px 8px;"></th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div>` : '<p style="margin:10px 0 0; color:#64748b; font-size:.72rem;">아직 보관된 검토 패킷이 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsCandidateQueueHistoryHtml(data) {
  const latest = data.latest;
  if (!latest) return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">후보 큐 이력 · 검토 알림</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">아직 일별 후보 큐 기준선이 없습니다.</p></div><button type="button" id="stg-kqs-queue-snapshot-save" style="padding:7px 10px; color:var(--text); border:1px solid #334155; border-radius:7px; font-size:.72rem;">오늘 기준선 저장</button></header><small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
  const changes = latest.changes || {}; const labels = (items) => items?.map((item) => escapeHtml(item.name || item.symbol)).join(', ') || '없음';
  const pending = data.review_summary?.pending || [];
  const historyRows = (data.snapshots || []).slice(-7).reverse().map((item) => `<tr><td style="padding:6px 8px;">${escapeHtml(item.snapshot_date)}</td><td style="padding:6px 8px;">${item.candidates?.length ?? 0}</td><td style="padding:6px 8px; color:var(--up);">${item.changes?.new?.length ?? 0}</td><td style="padding:6px 8px; color:#f59e0b;">${item.changes?.removed?.length ?? 0}</td></tr>`).join('');
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">후보 큐 이력 · 검토 알림</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">최근 기준선 ${escapeHtml(latest.snapshot_date)} · 엔진 기준일 ${escapeHtml(latest.as_of || '-')}</p></div><button type="button" id="stg-kqs-queue-snapshot-save" style="padding:7px 10px; color:var(--text); border:1px solid #334155; border-radius:7px; font-size:.72rem;">오늘 기준선 저장</button></header><div style="display:grid; grid-template-columns:repeat(4,minmax(100px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>현재 후보</small><strong>${latest.candidates?.length ?? 0}</strong></div><div class="metric-box"><small>신규 후보</small><strong style="color:var(--up);">${changes.new?.length ?? 0}</strong></div><div class="metric-box"><small>소멸 후보</small><strong style="color:#f59e0b;">${changes.removed?.length ?? 0}</strong></div><div class="metric-box"><small>미검토</small><strong style="color:${pending.length ? '#f59e0b' : 'var(--up)'};">${pending.length}</strong></div></div><div style="display:grid; grid-template-columns:repeat(3,minmax(180px,1fr)); gap:10px; margin-top:12px;"><section style="padding:9px; border:1px solid #253247; border-radius:7px;"><h3 style="margin:0 0 6px; font-size:.73rem; color:var(--up);">신규 후보</h3><p style="margin:0; color:#94a3b8; font-size:.68rem; line-height:1.7;">${labels(changes.new)}</p></section><section style="padding:9px; border:1px solid #253247; border-radius:7px;"><h3 style="margin:0 0 6px; font-size:.73rem; color:#f59e0b;">소멸 후보</h3><p style="margin:0; color:#94a3b8; font-size:.68rem; line-height:1.7;">${labels(changes.removed)}</p></section><section style="padding:9px; border:1px solid #253247; border-radius:7px;"><h3 style="margin:0 0 6px; font-size:.73rem; color:${pending.length ? '#f59e0b' : 'var(--up)'};">검토 미완료</h3><p style="margin:0; color:#94a3b8; font-size:.68rem; line-height:1.7;">${labels(pending)}</p></section></div>${historyRows ? `<div style="overflow-x:auto; margin-top:12px;"><table style="width:100%; min-width:420px; border-collapse:collapse; font-size:.7rem;"><thead><tr style="color:#64748b; text-align:left;"><th style="padding:6px 8px;">날짜</th><th style="padding:6px 8px;">후보 수</th><th style="padding:6px 8px;">신규</th><th style="padding:6px 8px;">소멸</th></tr></thead><tbody style="color:var(--text);">${historyRows}</tbody></table></div>` : ''}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsCandidateReviewWorkflowHtml(data) {
  const summary = data.summary || {};
  if (!data.available) return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">후보 검토 우선순위 · 기한</h2><p style="margin:0; color:#94a3b8; font-size:.72rem;">후보 큐 기준선이 생성되면 검토 업무 기한을 표시합니다.</p></header><small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
  const attention = { overdue: ['기한 경과', '#ef4444'], due_soon: ['마감 임박', '#f59e0b'], open: ['검토 대기', '#94a3b8'], completed: ['기록 완료', 'var(--up)'] };
  const rows = (data.items || []).map((item) => { const state = attention[item.attention] || attention.open; return `<tr><td style="padding:6px 8px;">${escapeHtml(item.name || item.symbol)}</td><td style="padding:6px 8px;">${escapeHtml(item.symbol || '-')}</td><td style="padding:6px 8px; color:${item.priority === 'high' ? '#f59e0b' : '#94a3b8'};">${item.priority === 'high' ? '높음' : '일반'}</td><td style="padding:6px 8px; color:${state[1]};">${state[0]}</td><td style="padding:6px 8px;">${item.deadline_at ? escapeHtml(item.deadline_at.slice(0, 16).replace('T', ' ')) : '-'}</td></tr>`; }).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">후보 검토 우선순위 · 기한</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">기준선 ${escapeHtml(data.snapshot_date || '-')} · 최초 관측 후 ${data.policy?.sla_hours ?? '-'}시간 내 검토 · 다음 거래일 대상은 높은 우선순위</p></header><div style="display:grid; grid-template-columns:repeat(5,minmax(90px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>미검토</small><strong>${summary.pending_count ?? 0}</strong></div><div class="metric-box"><small>높은 우선</small><strong style="color:#f59e0b;">${summary.high_priority_pending_count ?? 0}</strong></div><div class="metric-box"><small>마감 임박</small><strong style="color:#f59e0b;">${summary.due_soon_count ?? 0}</strong></div><div class="metric-box"><small>기한 경과</small><strong style="color:#ef4444;">${summary.overdue_count ?? 0}</strong></div><div class="metric-box"><small>기록 완료</small><strong style="color:var(--up);">${summary.reviewed_count ?? 0}</strong></div></div>${rows ? `<div style="overflow-x:auto; margin-top:12px;"><table style="width:100%; min-width:580px; border-collapse:collapse; font-size:.7rem;"><thead><tr style="color:#64748b; text-align:left;"><th style="padding:6px 8px;">종목</th><th style="padding:6px 8px;">코드</th><th style="padding:6px 8px;">우선순위</th><th style="padding:6px 8px;">검토 상태</th><th style="padding:6px 8px;">검토 기한</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div>` : '<p style="margin:12px 0 0; color:#94a3b8; font-size:.72rem;">현재 미검토 후보가 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsReviewReportHtml(data) {
  const summary = data.summary || {};
  const rows = (data.daily || []).slice(-7).reverse().map((item) => `<tr><td style="padding:5px 7px;">${escapeHtml(item.snapshot_date || '-')}</td><td style="padding:5px 7px;">${item.candidate_count ?? 0}</td><td style="padding:5px 7px;">${item.reviewed_count ?? 0}</td><td style="padding:5px 7px;">${item.deferred_count ?? 0}</td><td style="padding:5px 7px;">${item.pending_count ?? 0}</td></tr>`).join('');
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">후보 검토 운영 리포트</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">최근 ${data.period_days}일 · 검토 기록과 SLA 응답 현황</p></div><button type="button" data-kqs-review-export="csv" style="padding:7px 9px; border:1px solid #22c55e; border-radius:6px; color:#22c55e; font-size:.7rem;">CSV 내보내기</button></header><div style="display:grid; grid-template-columns:repeat(5,minmax(90px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>후보 관측</small><strong>${summary.candidate_observations ?? 0}</strong></div><div class="metric-box"><small>검토 완료</small><strong style="color:var(--up);">${summary.reviewed_count ?? 0}</strong></div><div class="metric-box"><small>보류</small><strong style="color:#f59e0b;">${summary.deferred_count ?? 0}</strong></div><div class="metric-box"><small>기록률</small><strong>${summary.recorded_review_rate_pct == null ? '-' : `${summary.recorded_review_rate_pct}%`}</strong></div><div class="metric-box"><small>SLA 준수</small><strong>${summary.sla_compliance_rate_pct == null ? '-' : `${summary.sla_compliance_rate_pct}%`}</strong></div></div>${rows ? `<div style="overflow:auto; margin-top:10px;"><table style="width:100%; min-width:470px; border-collapse:collapse; font-size:.68rem;"><thead style="color:#64748b; text-align:left;"><tr><th style="padding:5px 7px;">날짜</th><th style="padding:5px 7px;">후보</th><th style="padding:5px 7px;">완료</th><th style="padding:5px 7px;">보류</th><th style="padding:5px 7px;">미검토</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div>` : '<p style="margin:10px 0 0; color:#64748b; font-size:.7rem;">기간 내 후보 기준선이 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsSelectionQualityHtml(data) {
  const summary = data.summary || {};
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">후보 선정 품질 · 전략 성과 해석 경계</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">후보의 일별 연속성과 검토 범위만 측정합니다.</p></header><div style="display:grid; grid-template-columns:repeat(4,minmax(90px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>기준선</small><strong>${data.snapshot_count ?? 0}</strong></div><div class="metric-box"><small>후보 연속성</small><strong>${summary.candidate_continuity_rate_pct == null ? '-' : `${summary.candidate_continuity_rate_pct}%`}</strong></div><div class="metric-box"><small>신규 후보</small><strong>${summary.new_candidate_count ?? 0}</strong></div><div class="metric-box"><small>검토 범위</small><strong>${summary.review_coverage_pct == null ? '-' : `${summary.review_coverage_pct}%`}</strong></div></div><small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsOperationsAlertsHtml(data) {
  const summary = data.summary || {};
  const color = { critical: '#ef4444', warning: '#f59e0b', info: '#38bdf8' };
  const rows = (data.alerts || []).slice(0, 8).map((item) => `<li style="color:${color[item.severity] || '#94a3b8'}; margin-bottom:5px;">${escapeHtml(item.title)} <span style="color:#94a3b8;">· ${escapeHtml(item.message)}</span><span style="color:${item.acknowledgement_sla === 'overdue' ? '#ef4444' : '#64748b'};"> · ${item.acknowledgement ? '확인 기록됨' : item.acknowledgement_sla === 'overdue' ? '확인 SLA 경과' : '확인 대기'}</span>${!item.acknowledgement ? ` <button type="button" data-kqs-operation-ack="acknowledged" data-kqs-operation-fingerprint="${escapeHtml(item.fingerprint)}" style="font-size:.62rem; padding:2px 5px;">확인</button> <button type="button" data-kqs-operation-ack="deferred" data-kqs-operation-fingerprint="${escapeHtml(item.fingerprint)}" style="font-size:.62rem; padding:2px 5px;">보류</button>` : ''}</li>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">데이터·파이프라인 운영 경고</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">후보 기준선 최신성, 검토 SLA, 시세 데이터 품질을 점검합니다. 확인은 사용자 메모이며 원본 경고를 해소하지 않습니다.</p></header><div style="display:grid; grid-template-columns:repeat(4,minmax(90px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>활성 경고</small><strong>${summary.active_alert_count ?? 0}</strong></div><div class="metric-box"><small>심각</small><strong style="color:#ef4444;">${summary.critical_count ?? 0}</strong></div><div class="metric-box"><small>주의</small><strong style="color:#f59e0b;">${summary.warning_count ?? 0}</strong></div><div class="metric-box"><small>확인 SLA 경과</small><strong style="color:#ef4444;">${summary.acknowledgement_overdue_count ?? 0}</strong></div></div>${rows ? `<ul style="margin:10px 0 0; padding-left:17px; font-size:.68rem; line-height:1.75;">${rows}</ul>` : '<p style="margin:10px 0 0; color:var(--up); font-size:.72rem;">현재 저장된 운영 경고가 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function strategyGovernanceHtml(data) {
  const summary = data.summary || {};
  const candidateQuality = data.candidate_selection_quality?.summary || {};
  const label = { human_review_candidate: '사람 검토 후보', human_review_required: '재검토 필요', continue_paper_monitoring: '모의운용 계속' };
  const color = { human_review_candidate: '#22c55e', human_review_required: '#f59e0b', continue_paper_monitoring: '#38bdf8' };
  const rows = (data.items || []).map((item) => `<tr><td style="padding:6px 8px;">${escapeHtml(item.strategy_name)} v${item.strategy_version}</td><td style="padding:6px 8px; color:${color[item.recommendation] || '#94a3b8'};">${label[item.recommendation] || item.recommendation}</td><td style="padding:6px 8px;">${item.metrics?.observed_trading_days ?? 0}일</td><td style="padding:6px 8px;">${item.reasons?.[0] ? escapeHtml(item.reasons[0]) : '-'}</td></tr>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">전략 승격·중단 검토 보드</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">장기 모의운용 증거와 후보 선정 연속성을 함께 보되 상태를 자동 변경하지 않습니다.</p></header><div style="display:grid; grid-template-columns:repeat(6,minmax(90px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>승인 전략</small><strong>${summary.approved_strategy_count ?? 0}</strong></div><div class="metric-box"><small>검토 후보</small><strong style="color:var(--up);">${summary.live_review_candidate_count ?? 0}</strong></div><div class="metric-box"><small>재검토 필요</small><strong style="color:#f59e0b;">${summary.review_required_count ?? 0}</strong></div><div class="metric-box"><small>모니터링</small><strong>${summary.monitoring_count ?? 0}</strong></div><div class="metric-box"><small>후보 연속성</small><strong>${candidateQuality.candidate_continuity_rate_pct == null ? '-' : `${candidateQuality.candidate_continuity_rate_pct}%`}</strong></div><div class="metric-box"><small>후보 검토 범위</small><strong>${candidateQuality.review_coverage_pct == null ? '-' : `${candidateQuality.review_coverage_pct}%`}</strong></div></div>${rows ? `<div style="overflow:auto; margin-top:10px;"><table style="width:100%; min-width:600px; border-collapse:collapse; font-size:.68rem;"><thead style="color:#64748b; text-align:left;"><tr><th style="padding:6px 8px;">전략</th><th style="padding:6px 8px;">판정</th><th style="padding:6px 8px;">관측</th><th style="padding:6px 8px;">사유</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div>` : '<p style="margin:10px 0 0; color:#64748b; font-size:.72rem;">승인 고정된 전략이 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsCandidateOutcomesHtml(data) {
  const horizons = data.summary?.horizons || {};
  const metric = (days) => horizons[String(days)] || {};
  const rows = (data.rows || []).slice(0, 10).map((item) => `<tr><td style="padding:5px 7px;">${escapeHtml(item.as_of || '-')}</td><td style="padding:5px 7px;">${escapeHtml(item.name || item.symbol || '-')}</td><td style="padding:5px 7px;">${item.horizons?.['1']?.return_pct == null ? '-' : `${item.horizons['1'].return_pct}%`}</td><td style="padding:5px 7px;">${item.horizons?.['5']?.return_pct == null ? '-' : `${item.horizons['5'].return_pct}%`}</td><td style="padding:5px 7px;">${item.horizons?.['20']?.excess_return_pct == null ? '-' : `${item.horizons['20'].excess_return_pct}%p`}</td></tr>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">후보 사후성과 관측</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">선정 기준일 이후 검증된 일봉만 사용한 1·5·20거래일 관측치</p></header><div style="display:grid; grid-template-columns:repeat(3,minmax(100px,1fr)); gap:8px; margin-top:10px;">${[1, 5, 20].map((days) => `<div class="metric-box"><small>${days}거래일 관측</small><strong>${metric(days).observed_count ?? 0}</strong><small style="color:${metric(days).sample_gate === 'ready' ? 'var(--up)' : '#f59e0b'};">${metric(days).sample_gate === 'ready' ? '표본 기준 충족' : '표본 부족'} · 평균 ${metric(days).average_return_pct == null ? '-' : `${metric(days).average_return_pct}%`}</small></div>`).join('')}</div>${rows ? `<div style="overflow:auto; margin-top:10px;"><table style="width:100%; min-width:520px; border-collapse:collapse; font-size:.68rem;"><thead style="color:#64748b; text-align:left;"><tr><th style="padding:5px 7px;">기준일</th><th style="padding:5px 7px;">종목</th><th style="padding:5px 7px;">1일</th><th style="padding:5px 7px;">5일</th><th style="padding:5px 7px;">20일 KOSPI 대비</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div>` : '<p style="margin:10px 0 0; color:#64748b; font-size:.72rem;">사후성과를 계산할 수 있는 후보·일봉 관측치가 아직 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function strategyResearchRegistryHtml(data) {
  const items = data.items || [];
  const options = items.map((item) => `<option value="${escapeHtml(item.research_id)}">${escapeHtml(item.ticker || '-')} · ${escapeHtml(item.created_at?.slice(0, 16).replace('T', ' ') || '-')} · ${item.parameters?.fast_ma}/${item.parameters?.slow_ma}</option>`).join('');
  const rows = items.slice(0, 10).map((item) => `<tr><td style="padding:5px 7px;">${escapeHtml(item.ticker || '-')}</td><td style="padding:5px 7px;">${item.parameters?.fast_ma ?? '-'}/${item.parameters?.slow_ma ?? '-'}</td><td style="padding:5px 7px;">${item.final_test?.sharpe ?? '-'}</td><td style="padding:5px 7px;">${item.final_test?.total_return == null ? '-' : `${(item.final_test.total_return * 100).toFixed(2)}%`}</td><td style="padding:5px 7px;">${item.point_in_time_audit_passed ? '통과' : '확인 필요'}</td><td style="padding:5px 7px; color:${item.statistical_validation?.status === 'screen_passed' ? 'var(--up)' : '#f59e0b'};">${item.statistical_validation?.available ? (item.statistical_validation.status === 'screen_passed' ? '강건성 점검 통과' : '통계 재검토') : '-'}</td></tr>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">전략 실험 레지스트리 · A/B 비교</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">저장된 연구의 최종 테스트를 비교하며 전략 상태를 바꾸지 않습니다.</p></header>${items.length >= 2 ? `<div style="display:flex; gap:7px; align-items:end; flex-wrap:wrap; margin-top:10px;"><label style="display:grid; gap:4px; font-size:.68rem; color:#94a3b8;">기준<select id="stg-research-control" class="param-input" style="width:220px;">${options}</select></label><label style="display:grid; gap:4px; font-size:.68rem; color:#94a3b8;">도전자<select id="stg-research-challenger" class="param-input" style="width:220px;">${options}</select></label><button type="button" id="stg-research-compare" style="padding:7px 9px; border:1px solid #38bdf8; border-radius:6px; color:#38bdf8; font-size:.7rem;">A/B 비교</button></div><div id="stg-research-ab-result" style="margin-top:8px; color:#94a3b8; font-size:.7rem;"></div>` : '<p style="margin:10px 0 0; color:#64748b; font-size:.72rem;">A/B 비교에는 같은 티커의 저장된 연구가 최소 2건 필요합니다.</p>'}${rows ? `<div style="overflow:auto; margin-top:10px;"><table style="width:100%; min-width:650px; border-collapse:collapse; font-size:.68rem;"><thead style="color:#64748b; text-align:left;"><tr><th style="padding:5px 7px;">티커</th><th style="padding:5px 7px;">MA</th><th style="padding:5px 7px;">최종 Sharpe</th><th style="padding:5px 7px;">최종 수익률</th><th style="padding:5px 7px;">시점 감사</th><th style="padding:5px 7px;">통계 점검</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div>` : ''}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function recoveryReadinessHtml(data) {
  const checks = (data.checks || []).map((item) => `<li style="color:${item.passed ? 'var(--up)' : '#f59e0b'};">${item.passed ? '준비됨' : '확인 필요'} · ${escapeHtml(item.name)}</li>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">백업·복구 훈련 준비 상태</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">최근 백업 ${escapeHtml(data.latest_backup?.filename || '없음')} · 임시 DB 훈련만 허용</p></header><ul style="margin:10px 0 0; padding-left:17px; font-size:.7rem; line-height:1.8;">${checks}</ul><small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function liveIntegrationReadinessHtml(data) {
  const gates = (data.gates || []).map((item) => `<li style="color:${item.passed ? 'var(--up)' : '#f59e0b'};">${item.passed ? '통과' : '미충족'} · ${escapeHtml(item.name)} <span style="color:#94a3b8;">${escapeHtml(item.detail || '')}</span></li>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">실거래 연동 준비 관문</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">현재 주문 전송은 항상 비활성입니다.</p></header><ul style="margin:10px 0 0; padding-left:17px; font-size:.7rem; line-height:1.8;">${gates}</ul><small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function performanceDecompositionHtml(data) {
  const strategyRows = (data.strategy_items || []).map((item) => `<li>${escapeHtml(item.strategy_name)} · 모의 참고 ${item.paper_return_proxy_pct == null ? '-' : `${item.paper_return_proxy_pct}%`} · 백테스트 괴리 ${item.performance_gap_pct == null ? '-' : `${item.performance_gap_pct}%p`}</li>`).join('');
  const candidateRows = (data.candidate_groups?.items || []).filter((item) => item.dimension === 'market').map((item) => { const metric = item.horizons?.['20'] || {}; return `<li>${escapeHtml(item.value)} · 20일 관측 ${metric.observed_count ?? 0} · 평균 ${metric.average_return_pct == null ? '-' : `${metric.average_return_pct}%`} · ${metric.sample_gate === 'ready' ? '표본 충족' : '표본 부족'}</li>`; }).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">전략군·후보군 성과 분해</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">서로 다른 측정 범위를 분리해 표시합니다.</p></header><div style="display:grid; grid-template-columns:repeat(2,minmax(220px,1fr)); gap:10px; margin-top:10px;"><section style="padding:9px; border:1px solid #253247; border-radius:7px;"><h3 style="margin:0 0 6px; font-size:.73rem;">전략 모의운용</h3><ul style="margin:0; padding-left:16px; color:#94a3b8; font-size:.68rem; line-height:1.7;">${strategyRows || '<li>관측된 전략 모의성과가 없습니다.</li>'}</ul></section><section style="padding:9px; border:1px solid #253247; border-radius:7px;"><h3 style="margin:0 0 6px; font-size:.73rem;">후보군 사후성과</h3><ul style="margin:0; padding-left:16px; color:#94a3b8; font-size:.68rem; line-height:1.7;">${candidateRows || '<li>표시할 후보군 관측치가 없습니다.</li>'}</ul></section></div><small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function decisionRecordsHtml(data) {
  const label = { continue_monitoring: '모니터링 계속', review_required: '재검토 필요', stop_consideration: '중단 고려' };
  const rows = (data.items || []).slice(0, 8).map((item) => `<li>${escapeHtml(item.created_at?.slice(0, 16).replace('T', ' ') || '-')} · ${escapeHtml(item.target_type)} ${escapeHtml(item.target_id)} · ${label[item.decision] || item.decision}${item.note ? ` · ${escapeHtml(item.note)}` : ''}</li>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">전략 중단·재검토 의사결정 이력</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">기록은 판단 근거만 남기며 상태를 자동 변경하지 않습니다.</p></header><div style="display:flex; gap:7px; flex-wrap:wrap; align-items:end; margin-top:10px;"><label style="display:grid; gap:3px; font-size:.67rem; color:#94a3b8;">대상<select id="stg-decision-target-type" class="param-input" style="width:110px;"><option value="candidate">후보</option><option value="strategy">전략</option></select></label><label style="display:grid; gap:3px; font-size:.67rem; color:#94a3b8;">식별자<input id="stg-decision-target-id" class="param-input" style="width:160px;" placeholder="종목/전략 ID"></label><label style="display:grid; gap:3px; font-size:.67rem; color:#94a3b8;">결정<select id="stg-decision-value" class="param-input" style="width:130px;"><option value="continue_monitoring">모니터링 계속</option><option value="review_required">재검토 필요</option><option value="stop_consideration">중단 고려</option></select></label><button type="button" id="stg-decision-save" style="padding:7px 9px; border:1px solid #f59e0b; border-radius:6px; color:#f59e0b; font-size:.7rem;">기록</button></div>${rows ? `<ul style="margin:10px 0 0; padding-left:17px; color:#94a3b8; font-size:.68rem; line-height:1.8;">${rows}</ul>` : '<p style="margin:10px 0 0; color:#64748b; font-size:.72rem;">아직 의사결정 기록이 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function monthlyOperationsReportHtml(data) {
  const outcomes = data.candidate_outcomes?.summary || {}; const gate = data.candidate_outcomes?.quality_gate || {};
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">월간 퀀트 운영 리포트</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">${escapeHtml(data.month || '-')} · 예정 스냅샷 ${escapeHtml(data.scheduled_snapshot_captured_at?.slice(0, 16).replace('T', ' ') || '미생성')}</p></header><div style="display:grid; grid-template-columns:repeat(4,minmax(90px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>운영 경고</small><strong>${data.operations?.risk_and_review?.active_alert_count ?? 0}</strong></div><div class="metric-box"><small>후보 관측</small><strong>${outcomes.candidate_count ?? 0}</strong></div><div class="metric-box"><small>표본 기준</small><strong>${gate.minimum_sample_size ?? '-'}</strong></div><div class="metric-box"><small>실거래 전송</small><strong style="color:#f59e0b;">${data.live_integration?.live_order_submission_available ? '가능' : '차단'}</strong></div></div><p style="margin:10px 0 0; color:#64748b; font-size:.68rem;">복구 준비 · ${data.recovery?.latest_backup?.filename ? '최근 백업 확인' : '백업 확인 필요'} · 월간 감사 스냅샷은 매월 1일 09:30 KST에 저장됩니다.</p><small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function dataLineageHtml(data) {
  const summary = data.summary || {};
  const rows = (data.items || []).map((item) => `<tr><td style="padding:5px 7px;">${escapeHtml(item.ticker)}</td><td style="padding:5px 7px; color:${item.status === 'reproducible' ? 'var(--up)' : '#f59e0b'};">${item.status === 'reproducible' ? '재현 가능' : item.status === 'missing' ? '데이터 없음' : '메타데이터 보완 필요'}</td><td style="padding:5px 7px;">${item.bar_count ?? 0}</td><td style="padding:5px 7px;">${escapeHtml(item.last_timestamp || '-')}</td><td style="padding:5px 7px;">${escapeHtml((item.sources || []).join(', ') || '-')}</td></tr>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">연구 입력 데이터 계보</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">${escapeHtml(data.scope || '저장된 일봉')} · 지문 ${escapeHtml((data.report_fingerprint || '').slice(0, 12) || '-')}</p></header><div style="display:grid; grid-template-columns:repeat(3,minmax(90px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>대상</small><strong>${summary.ticker_count ?? 0}</strong></div><div class="metric-box"><small>재현 가능</small><strong style="color:var(--up);">${summary.reproducible_count ?? 0}</strong></div><div class="metric-box"><small>보완 필요/없음</small><strong style="color:#f59e0b;">${(summary.incomplete_count ?? 0) + (summary.missing_count ?? 0)}</strong></div></div>${rows ? `<div style="overflow:auto; margin-top:10px;"><table style="width:100%; min-width:560px; border-collapse:collapse; font-size:.68rem;"><thead style="color:#64748b; text-align:left;"><tr><th style="padding:5px 7px;">종목</th><th style="padding:5px 7px;">상태</th><th style="padding:5px 7px;">봉 수</th><th style="padding:5px 7px;">최근 기준일</th><th style="padding:5px 7px;">출처</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div>` : ''}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function revalidationMonitorHtml(data) {
  const summary = data.summary || {};
  const label = { current: '최신', baseline_only: '기준만 저장', refresh_required: '재검증 필요', review_required: '재검토 필요' };
  const color = { current: 'var(--up)', baseline_only: '#94a3b8', refresh_required: '#f59e0b', review_required: '#ef4444' };
  const rows = (data.items || []).slice(0, 10).map((item) => `<tr><td style="padding:5px 7px;">${escapeHtml(item.ticker || '-')}</td><td style="padding:5px 7px; color:${color[item.status] || '#94a3b8'};">${label[item.status] || item.status}</td><td style="padding:5px 7px;">${item.baseline?.sharpe ?? '-'}</td><td style="padding:5px 7px;">${item.deltas?.sharpe == null ? '-' : item.deltas.sharpe}</td><td style="padding:5px 7px;"><button type="button" data-revalidate-research="${escapeHtml(item.research_id)}" style="font-size:.62rem; padding:3px 6px;">최신 데이터로 재검증</button></td></tr>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">워크포워드 재검증</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">월간 근거 스냅샷 · 최신 데이터 재실행은 직접 요청할 때만 수행</p></header><div style="display:grid; grid-template-columns:repeat(3,minmax(90px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>최신</small><strong style="color:var(--up);">${summary.current_count ?? 0}</strong></div><div class="metric-box"><small>재검증 필요</small><strong style="color:#f59e0b;">${summary.refresh_required_count ?? 0}</strong></div><div class="metric-box"><small>재검토 필요</small><strong style="color:#ef4444;">${summary.review_required_count ?? 0}</strong></div></div>${rows ? `<div style="overflow:auto; margin-top:10px;"><table style="width:100%; min-width:560px; border-collapse:collapse; font-size:.68rem;"><thead style="color:#64748b; text-align:left;"><tr><th style="padding:5px 7px;">연구</th><th style="padding:5px 7px;">상태</th><th style="padding:5px 7px;">기준 Sharpe</th><th style="padding:5px 7px;">변화</th><th style="padding:5px 7px;"></th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div>` : '<p style="margin:10px 0 0; color:#64748b; font-size:.72rem;">저장된 워크포워드 연구가 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function integratedRiskHtml(data) {
  const rows = (data.items || []).slice(0, 8).map((item) => `<li>${escapeHtml(item.ticker)} · ${(item.combined_proxy_weight_pct ?? 0).toFixed(2)}% · ${item.cross_ledger_duplicate ? '<span style="color:#f59e0b;">실제/모의 중복</span>' : '단일 장부'}</li>`).join('');
  const scenario = data.stress_scenarios?.[0] || {};
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">포트폴리오 통합 리스크</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">실제 보유 장부와 모의 원장을 나란히 본 노출 프록시</p></header><div style="display:grid; grid-template-columns:repeat(3,minmax(100px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>병렬 장부 프록시</small><strong>${money(data.total_parallel_ledger_proxy_krw)}</strong></div><div class="metric-box"><small>점검 항목</small><strong style="color:#f59e0b;">${(data.findings || []).length}</strong></div><div class="metric-box"><small>-10% 충격 참고손실</small><strong style="color:#ef4444;">${money(scenario.estimated_proxy_loss_krw)}</strong></div></div>${rows ? `<ul style="margin:10px 0 0; padding-left:17px; color:#94a3b8; font-size:.68rem; line-height:1.8;">${rows}</ul>` : '<p style="margin:10px 0 0; color:#64748b; font-size:.72rem;">통합할 보유·모의 포지션이 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function operationsRunbookHtml(data) {
  const system = data.system_observability || {};
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">운영 경고·런북</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">활성 경고 ${(data.active_alerts || []).length}건 · 워크포워드 재검증 필요 ${data.revalidation?.refresh_required_count ?? 0}건</p></header><p style="margin:10px 0 0; color:#94a3b8; font-size:.7rem;">최근 일봉 수집 ${escapeHtml(system.latest_market_refresh?.status || '기록 없음')} · 계보 ${escapeHtml(system.latest_data_lineage?.snapshot_date || '기록 없음')} · 월간 감사 ${escapeHtml(system.latest_monthly_operations_report?.month || '기록 없음')}</p><p style="margin:6px 0 0; color:#94a3b8; font-size:.7rem;">런북: <code>${escapeHtml(data.runbook || '-')}</code> · 외부 메시지 발송 ${data.outbound_notifications ? '사용' : '사용 안 함'}</p><small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function sandboxContractHtml(data) {
  const checks = (data.checks || []).map((item) => `<li style="color:${item.passed ? 'var(--up)' : '#ef4444'};">${item.passed ? '통과' : '실패'} · ${escapeHtml(item.name)}</li>`).join('');
  return `<header><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">로컬 브로커 샌드박스 계약 검증</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">외부 네트워크·증권사 계좌·실거래 주문을 사용하지 않습니다.</p></header><button type="button" id="stg-run-sandbox-contract" style="margin-top:10px; padding:7px 9px; border:1px solid #38bdf8; border-radius:6px; color:#38bdf8; font-size:.7rem;">로컬 계약 검증 실행</button>${data.available === false ? '<p style="margin:10px 0 0; color:#64748b; font-size:.72rem;">아직 검증 기록이 없습니다.</p>' : `<ul style="margin:10px 0 0; padding-left:17px; font-size:.68rem; line-height:1.8;">${checks}</ul>`}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsReviewPacketHtml(data) {
  if (!data.available || !data.found) return `<h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">후보 검토 패킷</h2><p style="margin:0; color:#f59e0b; font-size:.72rem;">${escapeHtml(data.detail || '최신 검토 큐에서 후보를 찾지 못했습니다.')}</p>`;
  const candidate = data.candidate || {}; const evidence = data.selection_evidence || {}; const market = data.market_context || {}; const engine = data.engine_context || {}; const review = data.review;
  const reviewText = !review ? '아직 검토 기록이 없습니다.' : `${review.status === 'reviewed' ? '검토 완료' : '보류'}${review.note ? ` · ${review.note}` : ''}`;
  const liquidity = evidence.average_trading_value == null || evidence.average_trading_value === '' ? '-' : `₩${Math.round(Number(evidence.average_trading_value)).toLocaleString('ko-KR')}`;
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">후보 검토 패킷 · ${escapeHtml(candidate.name || candidate.symbol)}</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">${escapeHtml(candidate.symbol || '-')} · 엔진 기준일 ${escapeHtml(data.as_of || '-')} · ${escapeHtml(data.packet_version || '')}</p></div><button type="button" id="stg-kqs-packet-close" style="padding:7px 10px; color:#94a3b8; border:1px solid #334155; border-radius:7px; font-size:.72rem;">닫기</button></header><div style="display:grid; grid-template-columns:repeat(5,minmax(100px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>신호</small><strong>${escapeHtml(candidate.signal_type || '-')}</strong></div><div class="metric-box"><small>선정 결과</small><strong style="color:var(--up);">${escapeHtml(evidence.outcome || '-')}</strong></div><div class="metric-box"><small>스캔 상태</small><strong>${escapeHtml(evidence.scan_state || '-')}</strong></div><div class="metric-box"><small>시총 순위</small><strong>${escapeHtml(evidence.market_cap_rank ?? '-')}</strong></div><div class="metric-box"><small>평균 거래대금</small><strong>${escapeHtml(liquidity)}</strong></div></div><div style="display:grid; grid-template-columns:repeat(3,minmax(180px,1fr)); gap:10px; margin-top:12px;"><section style="padding:9px; border:1px solid #253247; border-radius:7px;"><h3 style="margin:0 0 6px; font-size:.73rem; color:var(--text);">선택 근거</h3><p style="margin:0; color:#94a3b8; font-size:.68rem; line-height:1.7;">시총 티어 ${escapeHtml(evidence.market_cap_tier ?? '-')} · 체결 시점 ${escapeHtml(candidate.execution_timing || '-')}</p></section><section style="padding:9px; border:1px solid #253247; border-radius:7px;"><h3 style="margin:0 0 6px; font-size:.73rem; color:var(--text);">시장 데이터</h3><p style="margin:0; color:#94a3b8; font-size:.68rem; line-height:1.7;">DB 기준일 ${escapeHtml(market.db_latest_trade_date || '-')} · ${escapeHtml(candidate.market || '-')} 행 ${escapeHtml(market.market_rows_on_latest ?? '-')} · 지연 ${escapeHtml(market.market_data_gap_days ?? '-')}일</p></section><section style="padding:9px; border:1px solid #253247; border-radius:7px;"><h3 style="margin:0 0 6px; font-size:.73rem; color:var(--text);">엔진·검토 상태</h3><p style="margin:0; color:#94a3b8; font-size:.68rem; line-height:1.7;">파이프라인 ${escapeHtml(engine.pipeline_status || '-')} · DB 갱신 ${engine.pipeline_db_advanced ? '확인' : '미확인'}<br>${escapeHtml(reviewText)}</p></section></div><small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsReconciliationHtml(data) {
  if (!data.available) return `<h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">내 보유자산 · 엔진 모의 포지션 대조</h2><p style="margin:0; color:#f59e0b; font-size:.72rem;">${escapeHtml(data.detail || '엔진 포지션을 확인하지 못했습니다.')}</p>`;
  const summary = data.summary || {};
  const holding = (item) => `<li>${escapeHtml(item.name || item.ticker)} <span style="color:#64748b;">${escapeHtml(item.ticker)} · ${item.quantity ?? '-'}주</span></li>`;
  const engine = (item) => `<li>${escapeHtml(item.name || item.symbol)} <span style="color:#64748b;">${escapeHtml(item.symbol)} · ${escapeHtml(item.signal_type || '-')} · 진입 ${escapeHtml(item.entry_date || '-')}</span></li>`;
  const matched = (item) => `<li>${escapeHtml(item.website.name || item.engine.name || item.symbol_key)} <span style="color:var(--up);">${escapeHtml(item.website.ticker)} ↔ ${escapeHtml(item.engine.symbol)}</span></li>`;
  const list = (items, renderer, empty) => items?.length ? `<ul style="margin:7px 0 0; padding-left:17px; color:var(--text); font-size:.68rem; line-height:1.8;">${items.map(renderer).join('')}</ul>` : `<p style="margin:7px 0 0; color:#64748b; font-size:.68rem;">${empty}</p>`;
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">내 보유자산 · 엔진 모의 포지션 대조</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">엔진 기준일 ${escapeHtml(data.as_of || '-')} · 종목 코드 대조</p></div><span style="color:#38bdf8; font-size:.7rem;">읽기 전용</span></header><div style="display:grid; grid-template-columns:repeat(5,minmax(85px,1fr)); gap:8px; margin-top:10px;"><div class="metric-box"><small>내 보유</small><strong>${summary.website_holding_count ?? '-'}</strong></div><div class="metric-box"><small>엔진 오픈</small><strong>${summary.engine_open_position_count ?? '-'}</strong></div><div class="metric-box"><small>종목 일치</small><strong style="color:var(--up);">${summary.matching_symbol_count ?? '-'}</strong></div><div class="metric-box"><small>내 보유만</small><strong style="color:#f59e0b;">${summary.website_only_count ?? '-'}</strong></div><div class="metric-box"><small>엔진만</small><strong style="color:#f59e0b;">${summary.engine_only_count ?? '-'}</strong></div></div><div style="display:grid; grid-template-columns:repeat(3,minmax(180px,1fr)); gap:10px; margin-top:12px;"><section style="padding:9px; border:1px solid #253247; border-radius:7px;"><h3 style="margin:0; font-size:.73rem; color:var(--up);">일치 종목</h3>${list(data.matched, matched, '일치 종목이 없습니다.')}</section><section style="padding:9px; border:1px solid #253247; border-radius:7px;"><h3 style="margin:0; font-size:.73rem; color:#f59e0b;">내 보유만</h3>${list(data.website_only, holding, '해당 없음')}</section><section style="padding:9px; border:1px solid #253247; border-radius:7px;"><h3 style="margin:0; font-size:.73rem; color:#f59e0b;">엔진만</h3>${list(data.engine_only, engine, '해당 없음')}</section></div><small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

function kqsReconciliationHistoryHtml(data) {
  const describe = (snapshot) => {
    const changes = snapshot.changes || {};
    if (changes.baseline) return '<span style="color:#64748b;">최초 기준선</span>';
    if (!changes.changed) return '<span style="color:var(--up);">변동 없음</span>';
    const labels = { matched: '일치', website_only: '내 보유만', engine_only: '엔진만' };
    const parts = [];
    for (const [kind, symbols] of Object.entries(changes.new || {})) if (symbols?.length) parts.push(`신규 ${labels[kind]} ${symbols.join(', ')}`);
    for (const [kind, symbols] of Object.entries(changes.resolved || {})) if (symbols?.length) parts.push(`해소 ${labels[kind]} ${symbols.join(', ')}`);
    return `<span style="color:#f59e0b;">${escapeHtml(parts.join(' · ') || '종목 집합 변동')}</span>`;
  };
  const rows = (data.snapshots || []).slice(-10).reverse().map((snapshot) => `<tr><td style="padding:6px 8px;">${escapeHtml(snapshot.snapshot_date)}</td><td style="padding:6px 8px;">${snapshot.summary?.matching_symbol_count ?? '-'}</td><td style="padding:6px 8px;">${snapshot.summary?.website_only_count ?? '-'}</td><td style="padding:6px 8px;">${snapshot.summary?.engine_only_count ?? '-'}</td><td style="padding:6px 8px;">${describe(snapshot)}</td></tr>`).join('');
  return `<header style="display:flex; justify-content:space-between; gap:10px; align-items:start; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">대조 이력 · 변동 알림</h2><p style="margin:0; color:#94a3b8; font-size:.7rem;">최근 ${data.days}일 · 변동 ${data.change_alert_count ?? 0}건</p></div><div style="display:flex; gap:7px;"><select id="stg-kqs-history-days" class="param-input" style="width:auto; padding:7px;"><option value="30" ${data.days === 30 ? 'selected' : ''}>30일</option><option value="90" ${data.days === 90 ? 'selected' : ''}>90일</option><option value="365" ${data.days === 365 ? 'selected' : ''}>1년</option></select><button type="button" id="stg-kqs-snapshot-save" style="padding:7px 10px; color:var(--text); border:1px solid #334155; border-radius:7px; font-size:.72rem;"><i class="fa-solid fa-camera"></i> 오늘 기준선 저장</button></div></header>${rows ? `<div style="overflow-x:auto; margin-top:10px;"><table style="width:100%; min-width:580px; border-collapse:collapse; font-size:.7rem;"><thead><tr style="color:#64748b; text-align:left;"><th style="padding:6px 8px;">날짜</th><th style="padding:6px 8px;">일치</th><th style="padding:6px 8px;">내 보유만</th><th style="padding:6px 8px;">엔진만</th><th style="padding:6px 8px;">전일 대비</th></tr></thead><tbody style="color:var(--text);">${rows}</tbody></table></div>` : '<p style="margin:10px 0 0; color:#64748b; font-size:.72rem;">아직 저장된 대조 기준선이 없습니다.</p>'}<small style="display:block; margin-top:8px; color:#64748b; font-size:.64rem;">${escapeHtml(data.disclaimer || '')}</small>`;
}

export function strategyListView(container) {
  if (!isAuthenticated()) {
    container.innerHTML = `<section style="max-width:600px; margin:64px auto; padding:34px; background:#12161f; border:1px solid #232938; border-radius:14px; text-align:center;">
      <h1 style="color:var(--text); font-size:1.3rem; margin:0 0 10px;">내 전략을 보려면 로그인하세요</h1>
      <p style="color:#94a3b8; font-size:.85rem; margin:0 0 20px;">저장한 실제 가격·가상 시뮬레이션·LEAN 백테스트를 확인하고, 실제 가격 전략은 주문 제안·모의 체결까지 관리할 수 있습니다.</p>
      <button type="button" class="run-btn" id="stg-login">로그인하고 시작하기</button>
    </section>`;
    container.querySelector('#stg-login').addEventListener('click', openAuthModal);
    return;
  }

  container.innerHTML = `
    <div style="margin-bottom:24px;">
      <h1 style="font-size:1.25rem; font-weight:700; color:var(--text); margin-bottom:6px;"><i class="fa-solid fa-folder-open"></i> 내 전략</h1>
      <p style="font-size:0.875rem; color:var(--text-muted); line-height:1.6;">저장한 실제 가격·가상 시뮬레이션·LEAN 백테스트를 확인하고, 실제 가격 전략은 주문 제안과 모의 체결까지 관리합니다.</p>
    </div>
    <div id="stg-list"><p style="color:#94a3b8;">불러오는 중...</p></div>
    <div id="stg-strategy-portfolio" style="margin-top:20px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">전략 배분안을 불러오는 중...</p></div>
    <div id="stg-rebalance-plan" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">리밸런싱 계획을 불러오는 중...</p></div>
    <div id="stg-promotion-monitor" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">승인 전략 점검을 불러오는 중...</p></div>
    <div id="stg-live-review-readiness" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">장기 모의운용 적합성을 불러오는 중...</p></div>
    <div id="stg-operations-report" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">주간 운영 리포트를 불러오는 중...</p></div>
    <div id="stg-kqs-status" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">외부 퀀트 엔진 상태를 불러오는 중...</p></div>
    <div id="stg-kqs-candidates" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">엔진 신규 후보 검토 큐를 불러오는 중...</p></div>
    <div id="stg-kqs-candidate-queue-history" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">후보 큐 이력과 검토 알림을 불러오는 중...</p></div>
    <div id="stg-kqs-review-workflow" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">후보 검토 우선순위와 기한을 불러오는 중...</p></div>
    <div id="stg-kqs-review-report" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">후보 검토 운영 리포트를 불러오는 중...</p></div>
    <div id="stg-kqs-selection-quality" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">후보 선정 품질을 불러오는 중...</p></div>
    <div id="stg-kqs-operations-alerts" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">데이터·파이프라인 운영 경고를 불러오는 중...</p></div>
    <div id="stg-strategy-governance" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">전략 승격·중단 검토 보드를 불러오는 중...</p></div>
    <div id="stg-kqs-outcomes" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">후보 사후성과를 불러오는 중...</p></div>
    <div id="stg-performance-decomposition" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">전략군·후보군 성과 분해를 불러오는 중...</p></div>
    <div id="stg-decision-records" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">전략 의사결정 이력을 불러오는 중...</p></div>
    <div id="stg-monthly-operations-report" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">월간 운영 리포트를 불러오는 중...</p></div>
    <div id="stg-data-lineage" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">연구 입력 데이터 계보를 불러오는 중...</p></div>
    <div id="stg-revalidation-monitor" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">워크포워드 재검증을 불러오는 중...</p></div>
    <div id="stg-integrated-risk" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">통합 리스크를 불러오는 중...</p></div>
    <div id="stg-operations-runbook" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">운영 런북을 불러오는 중...</p></div>
    <div id="stg-sandbox-contract" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">로컬 샌드박스 계약 검증 상태를 불러오는 중...</p></div>
    <div id="stg-research-registry" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">전략 실험 레지스트리를 불러오는 중...</p></div>
    <div id="stg-recovery-readiness" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">백업·복구 준비 상태를 불러오는 중...</p></div>
    <div id="stg-live-integration-readiness" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">실거래 연동 준비 상태를 불러오는 중...</p></div>
    <div id="stg-kqs-review-packet" style="display:none; margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"></div>
    <div id="stg-kqs-candidate-review-history" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">후보 검토 감사 이력을 불러오는 중...</p></div>
    <div id="stg-kqs-candidate-review-snapshots" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">검토 시점 패킷 보관함을 불러오는 중...</p></div>
    <div id="stg-kqs-reconciliation" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">내 보유자산과 엔진 모의 포지션을 대조하는 중...</p></div>
    <div id="stg-kqs-reconciliation-history" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">대조 이력과 변동 알림을 불러오는 중...</p></div>
    <div id="stg-detail"></div>
    <div id="stg-execution-status" style="margin-top:20px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">주문 실행 상태를 불러오는 중...</p></div>
    <div id="stg-paper-dashboard" style="margin-top:20px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">모의 계좌 현황을 불러오는 중...</p></div>
    <div id="stg-paper-alerts" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">알림을 불러오는 중...</p></div>
    <div id="stg-paper-report" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">감사 리포트를 불러오는 중...</p></div>
    <div id="stg-paper-history" style="margin-top:12px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">일별 성과 기록을 불러오는 중...</p></div>
    <div id="stg-paper-account" style="margin-top:20px; background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">모의 계좌를 불러오는 중...</p></div>
    <div style="margin-top:28px;">
      <h2 style="font-size:1rem; color:var(--text); margin-bottom:10px;">전체 모의 체결 내역</h2>
      <p style="margin:0 0 10px; color:#64748b; font-size:.72rem;">주문 제안을 승인하면 여기에 기록됩니다. 실제 보유자산(포트폴리오 → 내 보유자산)과는 별개의 모의 기록입니다.</p>
      <div id="stg-fills" style="background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">불러오는 중...</p></div>
    </div>`;

  const listEl = container.querySelector('#stg-list');
  const detailEl = container.querySelector('#stg-detail');
  const fillsEl = container.querySelector('#stg-fills');
  const accountEl = container.querySelector('#stg-paper-account');
  const dashboardEl = container.querySelector('#stg-paper-dashboard');
  const historyEl = container.querySelector('#stg-paper-history');
  const alertsEl = container.querySelector('#stg-paper-alerts');
  const reportEl = container.querySelector('#stg-paper-report');
  const kqsEl = container.querySelector('#stg-kqs-status');
  const kqsCandidatesEl = container.querySelector('#stg-kqs-candidates');
  const kqsCandidateQueueHistoryEl = container.querySelector('#stg-kqs-candidate-queue-history');
  const kqsReviewWorkflowEl = container.querySelector('#stg-kqs-review-workflow');
  const kqsReviewReportEl = container.querySelector('#stg-kqs-review-report');
  const kqsSelectionQualityEl = container.querySelector('#stg-kqs-selection-quality');
  const kqsOperationsAlertsEl = container.querySelector('#stg-kqs-operations-alerts');
  const strategyGovernanceEl = container.querySelector('#stg-strategy-governance');
  const kqsOutcomesEl = container.querySelector('#stg-kqs-outcomes');
  const performanceDecompositionEl = container.querySelector('#stg-performance-decomposition');
  const decisionRecordsEl = container.querySelector('#stg-decision-records');
  const monthlyOperationsReportEl = container.querySelector('#stg-monthly-operations-report');
  const dataLineageEl = container.querySelector('#stg-data-lineage');
  const revalidationMonitorEl = container.querySelector('#stg-revalidation-monitor');
  const integratedRiskEl = container.querySelector('#stg-integrated-risk');
  const operationsRunbookEl = container.querySelector('#stg-operations-runbook');
  const sandboxContractEl = container.querySelector('#stg-sandbox-contract');
  const researchRegistryEl = container.querySelector('#stg-research-registry');
  const recoveryReadinessEl = container.querySelector('#stg-recovery-readiness');
  const liveIntegrationReadinessEl = container.querySelector('#stg-live-integration-readiness');
  const kqsReviewPacketEl = container.querySelector('#stg-kqs-review-packet');
  const kqsCandidateReviewHistoryEl = container.querySelector('#stg-kqs-candidate-review-history');
  const kqsCandidateReviewSnapshotsEl = container.querySelector('#stg-kqs-candidate-review-snapshots');
  const kqsReconciliationEl = container.querySelector('#stg-kqs-reconciliation');
  const kqsReconciliationHistoryEl = container.querySelector('#stg-kqs-reconciliation-history');
  const executionEl = container.querySelector('#stg-execution-status');
  const strategyPortfolioEl = container.querySelector('#stg-strategy-portfolio');
  const rebalancePlanEl = container.querySelector('#stg-rebalance-plan');
  const promotionMonitorEl = container.querySelector('#stg-promotion-monitor');
  const liveReviewReadinessEl = container.querySelector('#stg-live-review-readiness');
  const operationsReportEl = container.querySelector('#stg-operations-report');

  async function loadStrategyPortfolio() {
    try { strategyPortfolioEl.innerHTML = strategyPortfolioHtml(await authRequest('/api/strategy/strategy-portfolio')); }
    catch (error) { strategyPortfolioEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }
  async function loadRebalancePlan() {
    try { rebalancePlanEl.innerHTML = rebalancePlanHtml(await authRequest('/api/strategy/strategy-rebalance-plan')); }
    catch (error) { rebalancePlanEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }
  async function loadPromotionMonitor() {
    try {
      const data = await authRequest('/api/strategy/promotion-monitor');
      promotionMonitorEl.innerHTML = promotionMonitorHtml(data);
      promotionMonitorEl.querySelector('#stg-promotion-monitor-refresh')?.addEventListener('click', async (event) => {
        event.currentTarget.disabled = true;
        try { await authRequest('/api/strategy/promotion-monitor/refresh', { method: 'POST' }); await Promise.all([loadPromotionMonitor(), loadPaperAlerts(false, false)]); }
        catch (error) { alert(error.message); event.currentTarget.disabled = false; }
      });
    } catch (error) { promotionMonitorEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }
  async function loadLiveReviewReadiness() {
    try { liveReviewReadinessEl.innerHTML = liveReviewReadinessHtml(await authRequest('/api/strategy/live-review-readiness')); }
    catch (error) { liveReviewReadinessEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }
  async function loadOperationsReport() {
    try { operationsReportEl.innerHTML = operationsReportHtml(await authRequest('/api/strategy/operations-report')); }
    catch (error) { operationsReportEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadKqsStatus() {
    try { kqsEl.innerHTML = kqsStatusHtml(await authRequest('/api/kqs/status')); }
    catch (error) { kqsEl.innerHTML = kqsStatusHtml({ available: false, detail: error.message }); }
  }

  async function loadKqsReviewCandidates() {
    try {
      const data = await authRequest('/api/kqs/review-candidates');
      kqsCandidatesEl.innerHTML = kqsReviewCandidatesHtml(data);
      kqsCandidatesEl.querySelector('#stg-kqs-candidates-refresh')?.addEventListener('click', loadKqsReviewCandidates);
      kqsCandidatesEl.querySelectorAll('[data-kqs-packet]').forEach((button) => button.addEventListener('click', () => loadKqsReviewPacket(button.dataset.kqsSymbol)));
      kqsCandidatesEl.querySelectorAll('[data-kqs-review-status]').forEach((button) => button.addEventListener('click', async (event) => {
        const status = event.currentTarget.dataset.kqsReviewStatus;
        const note = prompt(`${status === 'reviewed' ? '검토 완료' : '보류'} 메모(선택 사항)를 입력하세요.`);
        if (note === null) return;
        event.currentTarget.disabled = true;
        try {
          await authRequest(`/api/kqs/review-candidates/${encodeURIComponent(event.currentTarget.dataset.kqsSymbol)}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ as_of: data.as_of, status, note }) });
          await Promise.all([loadKqsReviewCandidates(), loadKqsCandidateQueueHistory(), loadKqsReviewWorkflow(), loadKqsReviewReport(), loadKqsSelectionQuality(), loadKqsOperationsAlerts(), loadKqsCandidateReviewHistory(), loadKqsCandidateReviewSnapshots()]);
        } catch (error) { alert(error.message); event.currentTarget.disabled = false; }
      }));
    } catch (error) { kqsCandidatesEl.innerHTML = kqsReviewCandidatesHtml({ available: false, detail: error.message }); }
  }

  async function loadKqsReviewPacket(symbol) {
    kqsReviewPacketEl.style.display = 'block';
    kqsReviewPacketEl.innerHTML = '<p style="color:#94a3b8; font-size:.8rem; margin:0;">검토 패킷을 불러오는 중...</p>';
    try {
      showKqsReviewPacket(await authRequest(`/api/kqs/review-candidates/${encodeURIComponent(symbol)}/packet`));
    } catch (error) { kqsReviewPacketEl.innerHTML = kqsReviewPacketHtml({ available: false, detail: error.message }); }
  }

  async function loadKqsCandidateReviewHistory() {
    try { kqsCandidateReviewHistoryEl.innerHTML = kqsCandidateReviewHistoryHtml(await authRequest('/api/kqs/review-candidates/reviews')); }
    catch (error) { kqsCandidateReviewHistoryEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadKqsCandidateQueueHistory() {
    try {
      const data = await authRequest('/api/kqs/candidate-queue/history');
      kqsCandidateQueueHistoryEl.innerHTML = kqsCandidateQueueHistoryHtml(data);
      kqsCandidateQueueHistoryEl.querySelector('#stg-kqs-queue-snapshot-save')?.addEventListener('click', async (event) => {
        event.currentTarget.disabled = true;
        try {
          await authRequest('/api/kqs/candidate-queue/snapshot', { method: 'POST' });
          await Promise.all([loadKqsReviewCandidates(), loadKqsCandidateQueueHistory(), loadKqsReviewWorkflow(), loadKqsReviewReport(), loadKqsSelectionQuality(), loadKqsOperationsAlerts()]);
        } catch (error) { alert(error.message); event.currentTarget.disabled = false; }
      });
    } catch (error) { kqsCandidateQueueHistoryEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadKqsReviewWorkflow() {
    try { kqsReviewWorkflowEl.innerHTML = kqsCandidateReviewWorkflowHtml(await authRequest('/api/kqs/candidate-queue/review-workflow')); }
    catch (error) { kqsReviewWorkflowEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function downloadKqsReviewReport(format) {
    const response = await fetch(`/api/kqs/candidate-queue/review-report/export?days=7&format=${encodeURIComponent(format)}`, { headers: { Authorization: `Bearer ${localStorage.getItem('investment_analysis_auth_token')}` } });
    if (!response.ok) { const payload = await response.json().catch(() => ({})); throw new Error(payload.detail || '후보 검토 리포트를 내보내지 못했습니다.'); }
    const url = URL.createObjectURL(await response.blob()); const link = document.createElement('a');
    link.href = url; link.download = `kqs-candidate-review-7d.${format}`; link.click(); URL.revokeObjectURL(url);
  }

  async function loadKqsReviewReport() {
    try {
      const data = await authRequest('/api/kqs/candidate-queue/review-report?days=7');
      kqsReviewReportEl.innerHTML = kqsReviewReportHtml(data);
      kqsReviewReportEl.querySelectorAll('[data-kqs-review-export]').forEach((button) => button.addEventListener('click', async () => {
        button.disabled = true;
        try { await downloadKqsReviewReport(button.dataset.kqsReviewExport); } catch (error) { alert(error.message); } finally { button.disabled = false; }
      }));
    } catch (error) { kqsReviewReportEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadKqsSelectionQuality() {
    try { kqsSelectionQualityEl.innerHTML = kqsSelectionQualityHtml(await authRequest('/api/kqs/candidate-queue/selection-quality?days=30')); }
    catch (error) { kqsSelectionQualityEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadKqsOperationsAlerts() {
    try {
      kqsOperationsAlertsEl.innerHTML = kqsOperationsAlertsHtml(await authRequest('/api/kqs/operations-alerts'));
      kqsOperationsAlertsEl.querySelectorAll('[data-kqs-operation-ack]').forEach((button) => button.addEventListener('click', async () => {
        const status = button.dataset.kqsOperationAck;
        const note = status === 'deferred' ? (prompt('보류 사유(선택 사항)를 입력하세요.') || '') : '';
        button.disabled = true;
        try { await authRequest(`/api/kqs/operations-alerts/${encodeURIComponent(button.dataset.kqsOperationFingerprint)}/acknowledgement`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status, note }) }); await loadKqsOperationsAlerts(); }
        catch (error) { alert(error.message); button.disabled = false; }
      }));
    }
    catch (error) { kqsOperationsAlertsEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadStrategyGovernance() {
    try { strategyGovernanceEl.innerHTML = strategyGovernanceHtml(await authRequest('/api/strategy/governance-board')); }
    catch (error) { strategyGovernanceEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadKqsCandidateOutcomes() {
    try { kqsOutcomesEl.innerHTML = kqsCandidateOutcomesHtml(await authRequest('/api/kqs/candidate-queue/outcomes?days=365')); }
    catch (error) { kqsOutcomesEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadPerformanceDecomposition() {
    try { performanceDecompositionEl.innerHTML = performanceDecompositionHtml(await authRequest('/api/strategy/performance-decomposition')); }
    catch (error) { performanceDecompositionEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadDecisionRecords() {
    try {
      decisionRecordsEl.innerHTML = decisionRecordsHtml(await authRequest('/api/strategy/decision-records'));
      decisionRecordsEl.querySelector('#stg-decision-save')?.addEventListener('click', async () => {
        const targetType = decisionRecordsEl.querySelector('#stg-decision-target-type').value;
        const targetId = decisionRecordsEl.querySelector('#stg-decision-target-id').value.trim();
        const decision = decisionRecordsEl.querySelector('#stg-decision-value').value;
        if (!targetId) { alert('기록할 후보 또는 전략 식별자를 입력하세요.'); return; }
        const note = prompt('결정 근거 메모(선택 사항)를 입력하세요.') || '';
        try {
          await authRequest('/api/strategy/decision-records', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target_type: targetType, target_id: targetId, decision, note }) });
          await loadDecisionRecords();
        } catch (error) { alert(error.message); }
      });
    } catch (error) { decisionRecordsEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadMonthlyOperationsReport() {
    try { monthlyOperationsReportEl.innerHTML = monthlyOperationsReportHtml(await authRequest('/api/strategy/monthly-operations-report')); }
    catch (error) { monthlyOperationsReportEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadDataLineage() {
    try { dataLineageEl.innerHTML = dataLineageHtml(await authRequest('/api/strategy/data-lineage')); }
    catch (error) { dataLineageEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadRevalidationMonitor() {
    try {
      revalidationMonitorEl.innerHTML = revalidationMonitorHtml(await authRequest('/api/strategy/research/revalidation-monitor'));
      revalidationMonitorEl.querySelectorAll('[data-revalidate-research]').forEach((button) => button.addEventListener('click', async () => {
        button.disabled = true;
        try { await authRequest(`/api/strategy/research/${encodeURIComponent(button.dataset.revalidateResearch)}/revalidate`, { method: 'POST' }); await loadRevalidationMonitor(); }
        catch (error) { alert(error.message); button.disabled = false; }
      }));
    } catch (error) { revalidationMonitorEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadIntegratedRisk() {
    try { integratedRiskEl.innerHTML = integratedRiskHtml(await authRequest('/api/strategy/integrated-risk')); }
    catch (error) { integratedRiskEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadOperationsRunbook() {
    try { operationsRunbookEl.innerHTML = operationsRunbookHtml(await authRequest('/api/strategy/operations-runbook')); }
    catch (error) { operationsRunbookEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadSandboxContract() {
    try {
      sandboxContractEl.innerHTML = sandboxContractHtml(await authRequest('/api/strategy/broker-sandbox/contract-check'));
      sandboxContractEl.querySelector('#stg-run-sandbox-contract')?.addEventListener('click', async (event) => {
        event.currentTarget.disabled = true;
        try { await authRequest('/api/strategy/broker-sandbox/contract-check', { method: 'POST' }); await loadSandboxContract(); await loadOperationsRunbook(); }
        catch (error) { alert(error.message); event.currentTarget.disabled = false; }
      });
    } catch (error) { sandboxContractEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadResearchRegistry() {
    try {
      const data = await authRequest('/api/strategy/research/registry');
      researchRegistryEl.innerHTML = strategyResearchRegistryHtml(data);
      researchRegistryEl.querySelector('#stg-research-compare')?.addEventListener('click', async () => {
        const control = researchRegistryEl.querySelector('#stg-research-control').value;
        const challenger = researchRegistryEl.querySelector('#stg-research-challenger').value;
        const resultEl = researchRegistryEl.querySelector('#stg-research-ab-result');
        if (control === challenger) { resultEl.textContent = '서로 다른 두 실험을 선택하세요.'; return; }
        try {
          const result = await authRequest(`/api/strategy/research/compare?control_id=${encodeURIComponent(control)}&challenger_id=${encodeURIComponent(challenger)}`);
          const delta = result.final_test_delta || {};
          resultEl.textContent = `최종 테스트 차이 · 수익률 ${delta.total_return == null ? '-' : delta.total_return.toFixed(4)} · Sharpe ${delta.sharpe == null ? '-' : delta.sharpe.toFixed(4)} · MDD ${delta.mdd == null ? '-' : delta.mdd.toFixed(4)}`;
        } catch (error) { resultEl.textContent = error.message; }
      });
    } catch (error) { researchRegistryEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadRecoveryReadiness() {
    try { recoveryReadinessEl.innerHTML = recoveryReadinessHtml(await authRequest('/api/strategy/recovery-readiness')); }
    catch (error) { recoveryReadinessEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadLiveIntegrationReadiness() {
    try { liveIntegrationReadinessEl.innerHTML = liveIntegrationReadinessHtml(await authRequest('/api/strategy/live-integration-readiness')); }
    catch (error) { liveIntegrationReadinessEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  function showKqsReviewPacket(packet) {
    kqsReviewPacketEl.style.display = 'block';
    kqsReviewPacketEl.innerHTML = kqsReviewPacketHtml(packet);
    kqsReviewPacketEl.querySelector('#stg-kqs-packet-close')?.addEventListener('click', () => { kqsReviewPacketEl.style.display = 'none'; kqsReviewPacketEl.innerHTML = ''; });
    kqsReviewPacketEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function loadKqsCandidateReviewSnapshots() {
    try {
      const data = await authRequest('/api/kqs/review-candidates/review-snapshots');
      kqsCandidateReviewSnapshotsEl.innerHTML = kqsCandidateReviewSnapshotHistoryHtml(data);
      kqsCandidateReviewSnapshotsEl.querySelectorAll('[data-kqs-archived-packet]').forEach((button) => button.addEventListener('click', () => showKqsReviewPacket(data.items[Number(button.dataset.kqsArchivedPacket)].packet)));
    } catch (error) { kqsCandidateReviewSnapshotsEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadKqsReconciliation() {
    try { kqsReconciliationEl.innerHTML = kqsReconciliationHtml(await authRequest('/api/kqs/portfolio-reconciliation')); }
    catch (error) { kqsReconciliationEl.innerHTML = kqsReconciliationHtml({ available: false, detail: error.message }); }
  }

  async function loadKqsReconciliationHistory(days = 30) {
    try {
      const data = await authRequest(`/api/kqs/portfolio-reconciliation/history?days=${encodeURIComponent(days)}`);
      kqsReconciliationHistoryEl.innerHTML = kqsReconciliationHistoryHtml(data);
      kqsReconciliationHistoryEl.querySelector('#stg-kqs-history-days')?.addEventListener('change', (event) => loadKqsReconciliationHistory(Number(event.target.value)));
      kqsReconciliationHistoryEl.querySelector('#stg-kqs-snapshot-save')?.addEventListener('click', async (event) => {
        event.currentTarget.disabled = true;
        try {
          await authRequest('/api/kqs/portfolio-reconciliation/snapshot', { method: 'POST' });
          await Promise.all([loadKqsReconciliation(), loadKqsReconciliationHistory(data.days)]);
        } catch (error) { alert(error.message); event.currentTarget.disabled = false; }
      });
    } catch (error) { kqsReconciliationHistoryEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadExecutionStatus() {
    try {
      const [data, audit] = await Promise.all([authRequest('/api/strategy/execution-status'), authRequest('/api/strategy/execution-audit')]);
      executionEl.innerHTML = executionStatusHtml(data, audit);
      executionEl.querySelector('#stg-paper-permission').addEventListener('click', async (event) => {
        const enabled = !data.user_permission.paper_trading_enabled;
        if (enabled && !confirm('모의 주문 제출을 다시 허용할까요?')) return;
        event.currentTarget.disabled = true;
        try { await authRequest('/api/strategy/execution-permission', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paper_trading_enabled: enabled }) }); await loadExecutionStatus(); }
        catch (error) { alert(error.message); event.currentTarget.disabled = false; }
      });
    } catch (error) { executionEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadPaperDashboard() {
    try {
      const data = await authRequest('/api/strategy/paper-dashboard');
      dashboardEl.innerHTML = paperDashboardHtml(data);
      dashboardEl.querySelector('#stg-dashboard-refresh')?.addEventListener('click', loadPaperDashboard);
    } catch (error) { dashboardEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadPaperHistory(period = '3m') {
    try {
      const data = await authRequest(`/api/strategy/paper-dashboard/history?period=${encodeURIComponent(period)}`);
      historyEl.innerHTML = paperHistoryHtml(data);
      historyEl.querySelector('#stg-history-period')?.addEventListener('change', (event) => loadPaperHistory(event.target.value));
      historyEl.querySelector('#stg-snapshot-save')?.addEventListener('click', async (event) => {
        event.currentTarget.disabled = true;
        try {
          await authRequest('/api/strategy/paper-dashboard/snapshot', { method: 'POST' });
          await Promise.all([loadPaperDashboard(), loadPaperHistory(data.period)]);
        } catch (error) { alert(error.message); event.currentTarget.disabled = false; }
      });
    } catch (error) { historyEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadPaperAlerts(history = false, refresh = false) {
    try {
      if (refresh) await authRequest('/api/strategy/alerts/refresh', { method: 'POST' });
      const data = await authRequest(`/api/strategy/alerts?status=${history ? 'history' : 'active'}`);
      alertsEl.innerHTML = paperAlertsHtml(data.items, history);
      alertsEl.querySelector('#stg-alert-refresh').addEventListener('click', () => loadPaperAlerts(history, true));
      alertsEl.querySelector('#stg-alert-history').addEventListener('click', () => loadPaperAlerts(!history, false));
      alertsEl.querySelectorAll('[data-alert-action]').forEach((button) => button.addEventListener('click', async () => {
        const action = button.dataset.alertAction;
        const row = button.closest('[data-paper-alert]');
        const note = action === 'deferred' ? (prompt('보류 사유(선택 사항)를 입력하세요.') || '') : '';
        button.disabled = true;
        try {
          await authRequest(`/api/strategy/alerts/${row.dataset.paperAlert}/resolve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, note }) });
          await loadPaperAlerts(history, false);
        } catch (error) { alert(error.message); button.disabled = false; }
      }));
    } catch (error) { alertsEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function downloadPaperReport(period, format) {
    const response = await fetch(`/api/strategy/paper-report/export?period=${encodeURIComponent(period)}&format=${format}`, { headers: { Authorization: `Bearer ${localStorage.getItem('investment_analysis_auth_token')}` } });
    if (!response.ok) { const payload = await response.json().catch(() => ({})); throw new Error(payload.detail || '리포트를 내보내지 못했습니다.'); }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob); const link = document.createElement('a');
    link.href = url; link.download = `paper-research-report-${period}.${format}`; link.click(); URL.revokeObjectURL(url);
  }

  async function loadPaperReport(period = '3m') {
    try {
      const data = await authRequest(`/api/strategy/paper-report?period=${encodeURIComponent(period)}`);
      reportEl.innerHTML = paperReportHtml(data);
      reportEl.querySelector('#stg-report-period').addEventListener('change', (event) => loadPaperReport(event.target.value));
      reportEl.querySelectorAll('[data-report-export]').forEach((button) => button.addEventListener('click', async () => {
        button.disabled = true;
        try { await downloadPaperReport(data.period, button.dataset.reportExport); } catch (error) { alert(error.message); } finally { button.disabled = false; }
      }));
    } catch (error) { reportEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadPaperAccount() {
    try {
      const account = await authRequest('/api/strategy/paper-account');
      const formatted = Number(account.initial_capital_krw).toLocaleString('ko-KR');
      accountEl.innerHTML = `<div style="display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap;"><div><h2 style="margin:0 0 5px; color:var(--text); font-size:.9rem;">모의 계좌 위험 통제</h2><p style="margin:0; color:#94a3b8; font-size:.72rem;">실제 보유자산과 분리된 KRW 가상자본입니다. 승인 시 현금·집중도 한도를 자동 점검합니다.</p></div><div style="color:var(--text); font-size:.85rem; font-weight:700;">₩${formatted}</div></div>${account.fill_count === 0 ? `<div style="display:flex; gap:8px; align-items:end; margin-top:12px;"><label style="display:grid; gap:4px; color:#94a3b8; font-size:.7rem;">첫 체결 전 가상자본 (KRW)<input id="stg-paper-capital" class="param-input" type="number" min="1000" step="1000" value="${Number(account.initial_capital_krw)}" style="width:180px;"></label><button type="button" class="run-btn" id="stg-paper-capital-save" style="padding:8px 10px; font-size:.72rem;">설정</button></div>` : `<p style="margin:10px 0 0; color:#64748b; font-size:.7rem;">체결 ${account.fill_count}건 이후에는 원장 일관성을 위해 가상자본을 바꿀 수 없습니다.</p>`}`;
      accountEl.querySelector('#stg-paper-capital-save')?.addEventListener('click', async (event) => {
        const value = Number(accountEl.querySelector('#stg-paper-capital').value);
        if (!Number.isFinite(value) || value < 1000) { alert('가상자본은 1,000 KRW 이상으로 입력하세요.'); return; }
        event.currentTarget.disabled = true;
        try {
          await authRequest('/api/strategy/paper-account', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ initial_capital_krw: value }) });
          await loadPaperAccount();
        } catch (error) { alert(error.message); event.currentTarget.disabled = false; }
      });
    } catch (error) { accountEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`; }
  }

  async function loadFills() {
    try {
      const data = await authRequest('/api/strategy/fills');
      fillsEl.innerHTML = data.items.length
        ? `<div style="overflow-x:auto;"><table style="width:100%; min-width:420px; border-collapse:collapse; font-size:.78rem;"><thead><tr style="color:#64748b; text-align:left;"><th style="padding:6px 10px;">시각</th><th style="padding:6px 10px;">종목</th><th style="padding:6px 10px;">방향</th><th style="padding:6px 10px;">수량</th><th style="padding:6px 10px;">체결가</th></tr></thead><tbody style="color:var(--text);">${data.items.map(fillRow).join('')}</tbody></table></div>`
        : '<p style="color:#94a3b8; font-size:.8rem; margin:0;">아직 모의 체결 내역이 없습니다.</p>';
    } catch (error) {
      fillsEl.innerHTML = `<p style="color:#ef4444; font-size:.8rem; margin:0;">${escapeHtml(error.message)}</p>`;
    }
  }

  async function loadProposals(strategyId, root) {
    try {
      const data = await authRequest(`/api/strategy/${strategyId}/proposals`);
      root.innerHTML = data.items.length
        ? `<div style="overflow-x:auto;"><table style="width:100%; min-width:480px; border-collapse:collapse; font-size:.78rem; margin-top:10px;"><thead><tr style="color:#64748b; text-align:left;"><th style="padding:8px 10px;">방향</th><th style="padding:8px 10px;">근거</th><th style="padding:8px 10px;">기준가</th><th style="padding:8px 10px;">상태</th><th style="padding:8px 10px;"></th></tr></thead><tbody style="color:var(--text);">${data.items.map(proposalRow).join('')}</tbody></table></div>`
        : '<p style="color:#94a3b8; font-size:.78rem; margin:10px 0 0;">대기 중인 주문 제안이 없습니다.</p>';

      root.querySelectorAll('[data-proposal-approve]').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const row = btn.closest('tr');
          const qty = +row.querySelector('[data-proposal-qty]').value;
          if (!qty || qty <= 0) { alert('수량을 입력하세요.'); return; }
          btn.disabled = true;
          try {
            const fill = await authRequest(`/api/strategy/proposals/${row.dataset.proposalId}/approve`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quantity: qty }),
            });
            if (fill.risk_control?.action === 'reduced') alert(`위험 한도에 맞춰 수량을 ${fill.quantity}로 자동 감액해 모의 체결했습니다.\n${(fill.risk_control.reasons || []).join('\n')}`);
            await loadProposals(strategyId, root);
            await loadFills();
            await loadPaperAccount();
            await loadPaperDashboard();
            await loadPaperHistory();
            await loadPaperAlerts(false, true);
            await loadPaperReport();
            await loadExecutionStatus();
          } catch (error) { alert(error.message); btn.disabled = false; }
        });
      });
      root.querySelectorAll('[data-proposal-reject]').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const row = btn.closest('tr');
          btn.disabled = true;
          try {
            await authRequest(`/api/strategy/proposals/${row.dataset.proposalId}/reject`, { method: 'POST' });
            await loadProposals(strategyId, root);
          } catch (error) { alert(error.message); btn.disabled = false; }
        });
      });
    } catch (error) {
      root.innerHTML = `<p style="color:#ef4444; font-size:.78rem;">${escapeHtml(error.message)}</p>`;
    }
  }

  async function loadList() {
    try {
      const data = await authRequest('/api/strategy');
      listEl.innerHTML = data.items.length
        ? data.items.map(strategyCard).join('')
        : '<p style="color:#94a3b8; font-size:.85rem;">아직 저장된 전략이 없습니다. 전략 백테스트·실제 가격 백테스트·LEAN 리포트 화면에서 "전략으로 저장"을 눌러보세요.</p>';
      listEl.querySelectorAll('[data-strategy-id]').forEach((card) => {
        card.addEventListener('click', () => showDetail(card.dataset.strategyId));
      });
    } catch (error) {
      listEl.innerHTML = `<p style="color:#ef4444;">${escapeHtml(error.message)}</p>`;
    }
  }

  async function showDetail(strategyId) {
    detailEl.innerHTML = '<p style="color:#94a3b8;">불러오는 중...</p>';
    try {
      const item = await authRequest(`/api/strategy/${strategyId}`);
      const m = item.snapshot?.metrics;
      const metricsHtml = m ? `<div style="display:flex; flex-wrap:wrap; gap:12px; margin-bottom:16px;">
          <div class="metric-box"><div style="font-size:.7rem; color:#64748b;">총수익률</div><div style="font-size:1rem; font-weight:700; color:var(--text);">${pct(m.total_return, 1)}</div></div>
          <div class="metric-box"><div style="font-size:.7rem; color:#64748b;">CAGR</div><div style="font-size:1rem; font-weight:700; color:var(--text);">${pct(m.cagr, 2)}</div></div>
          <div class="metric-box"><div style="font-size:.7rem; color:#64748b;">Sharpe</div><div style="font-size:1rem; font-weight:700; color:var(--text);">${m.sharpe?.toFixed(2) ?? '-'}</div></div>
          <div class="metric-box"><div style="font-size:.7rem; color:#64748b;">Sortino</div><div style="font-size:1rem; font-weight:700; color:var(--text);">${m.sortino?.toFixed(2) ?? '-'}</div></div>
          <div class="metric-box"><div style="font-size:.7rem; color:#64748b;">MDD</div><div style="font-size:1rem; font-weight:700; color:var(--text);">${pct(m.mdd, 1)}</div></div>
        </div>` : '';
      const statsHtml = item.type === 'lean' && item.snapshot?.statistics
        ? `<div style="display:flex; flex-wrap:wrap; gap:12px; margin-bottom:16px;">${Object.entries(item.snapshot.statistics).slice(0, 8).map(([k, v]) => `<div class="metric-box"><div style="font-size:.66rem; color:#64748b;">${escapeHtml(k)}</div><div style="font-size:.88rem; font-weight:700; color:var(--text);">${escapeHtml(v)}</div></div>`).join('')}</div>`
        : '';

      detailEl.innerHTML = `<div style="background:#12161f; border:1px solid #232938; border-radius:12px; padding:20px; margin-top:16px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; flex-wrap:wrap; gap:8px;">
          <h2 style="margin:0; color:var(--text); font-size:1.05rem;">${escapeHtml(item.name)}</h2>
          <div style="display:flex; gap:8px; align-items:center;">${badge(item.type)}<button type="button" data-delete-strategy style="padding:6px 10px; font-size:.72rem; color:#ef4444; background:none; border:1px solid #ef4444; border-radius:6px;">삭제</button></div>
        </div>
        ${metadataTable(item)}
        ${item.parent_id ? '<div id="stg-version-comparison"><p style="color:#94a3b8; font-size:.72rem;">이전 버전과 비교 중...</p></div>' : ''}
        ${metricsHtml}
        ${statsHtml}
        ${item.type === 'real_backtest' ? `
          <div style="border-top:1px solid #232938; padding-top:14px; margin-top:4px;">
            <div id="stg-order-preflight"><p style="margin:0 0 8px; color:#94a3b8; font-size:.72rem;">모의 주문 사전 점검 중...</p></div>
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
              <h3 style="margin:0; color:var(--text); font-size:.9rem;">주문 제안</h3>
              <button type="button" class="run-btn" id="stg-make-proposal" style="padding:8px 12px; font-size:.75rem;">현재 신호 확인 → 주문 제안 생성</button>
            </div>
            <p style="margin:8px 0 0; color:#64748b; font-size:.72rem;">최신 시세로 다시 계산한 신호입니다. 승인 시 입력한 수량으로 모의 체결이 기록되며, 실제 보유자산(내 보유자산)에는 반영되지 않습니다.</p>
            <div id="stg-proposals"></div>
          </div>` : ''}
      </div>`;

      detailEl.querySelector('[data-delete-strategy]').addEventListener('click', async () => {
        if (!confirm('이 전략을 삭제할까요?')) return;
        try {
          await authRequest(`/api/strategy/${strategyId}`, { method: 'DELETE' });
          detailEl.innerHTML = '';
          await loadList();
        } catch (error) { alert(error.message); }
      });

      if (item.type === 'real_backtest') {
        if (item.parent_id) {
          const comparisonRoot = detailEl.querySelector('#stg-version-comparison');
          try {
            const [comparison, promotion] = await Promise.all([
              authRequest(`/api/strategy/strategy-version-comparison/${item.id}`),
              authRequest(`/api/strategy/strategy-version-promotion/${item.id}`),
            ]);
            comparisonRoot.innerHTML = versionComparisonHtml(comparison) + versionPromotionHtml(promotion);
            comparisonRoot.querySelector('[data-promotion-approve]')?.addEventListener('click', async (event) => {
              if (!confirm('이 전략 버전을 승인 고정할까요? 주문·모의 체결·배분은 변경되지 않습니다.')) return;
              const note = prompt('승인 메모(선택 사항)를 입력하세요.') || '';
              event.currentTarget.disabled = true;
              try {
                await authRequest(`/api/strategy/strategy-version-promotion/${item.id}/approve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note }) });
                await Promise.all([loadList(), loadStrategyPortfolio(), loadRebalancePlan()]);
                await showDetail(strategyId);
              } catch (error) { alert(error.message); event.currentTarget.disabled = false; }
            });
            comparisonRoot.querySelector('[data-promotion-revoke]')?.addEventListener('click', async (event) => {
              if (!confirm('이 전략 버전의 승인 고정을 철회할까요? 주문·모의 체결·배분은 변경되지 않습니다.')) return;
              const note = prompt('철회 메모(선택 사항)를 입력하세요.') || '';
              event.currentTarget.disabled = true;
              try {
                await authRequest(`/api/strategy/strategy-version-promotion/${item.id}/revoke`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note }) });
                await showDetail(strategyId);
              } catch (error) { alert(error.message); event.currentTarget.disabled = false; }
            });
          }
          catch (error) { comparisonRoot.innerHTML = `<p style="color:#ef4444; font-size:.72rem;">${escapeHtml(error.message)}</p>`; }
        }
        const proposalsRoot = detailEl.querySelector('#stg-proposals');
        const preflightRoot = detailEl.querySelector('#stg-order-preflight');
        try {
          const preflight = await authRequest(`/api/strategy/paper-order-preflight/${item.id}`);
          preflightRoot.innerHTML = paperOrderPreflightHtml(preflight);
          if (!preflight.allowed) detailEl.querySelector('#stg-make-proposal').disabled = true;
        } catch (error) { preflightRoot.innerHTML = `<p style="margin:0 0 8px; color:#ef4444; font-size:.72rem;">${escapeHtml(error.message)}</p>`; }
        await loadProposals(strategyId, proposalsRoot);
        detailEl.querySelector('#stg-make-proposal').addEventListener('click', async (event) => {
          event.currentTarget.disabled = true;
          try {
            await authRequest(`/api/strategy/${strategyId}/proposals`, { method: 'POST' });
            await loadProposals(strategyId, proposalsRoot);
          } catch (error) { alert(error.message); } finally { event.currentTarget.disabled = false; }
        });
      }
    } catch (error) {
      detailEl.innerHTML = `<p style="color:#ef4444;">${escapeHtml(error.message)}</p>`;
    }
  }

  loadList();
  loadStrategyPortfolio();
  loadRebalancePlan();
  loadPromotionMonitor();
  loadLiveReviewReadiness();
  loadOperationsReport();
  loadKqsStatus();
  loadKqsReviewCandidates();
  loadKqsCandidateQueueHistory();
  loadKqsReviewWorkflow();
  loadKqsReviewReport();
  loadKqsSelectionQuality();
  loadKqsOperationsAlerts();
  loadStrategyGovernance();
  loadKqsCandidateOutcomes();
  loadPerformanceDecomposition();
  loadDecisionRecords();
  loadMonthlyOperationsReport();
  loadDataLineage();
  loadRevalidationMonitor();
  loadIntegratedRisk();
  loadOperationsRunbook();
  loadSandboxContract();
  loadResearchRegistry();
  loadRecoveryReadiness();
  loadLiveIntegrationReadiness();
  loadKqsCandidateReviewHistory();
  loadKqsCandidateReviewSnapshots();
  loadKqsReconciliation();
  loadKqsReconciliationHistory();
  loadFills();
  loadPaperAccount();
  loadPaperDashboard();
  loadPaperAlerts(false, true);
  loadPaperReport();
  loadExecutionStatus();
  loadPaperHistory();
}
