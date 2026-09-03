import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';

const PERIODS = [['6mo', '6개월'], ['1y', '1년'], ['2y', '2년'], ['3y', '3년'], ['5y', '5년']];
const pct = (value, digits = 2) => `${(Number(value) * 100).toFixed(digits)}%`;

function metricRow(label, strat, bench) {
  return `<tr><td>${label}</td><td>${strat}</td><td>${bench}</td></tr>`;
}

function renderResult(container, req, data) {
  const result = container.querySelector('#sbt-result');
  const m = data.metrics;
  const b = data.benchmark_metrics;
  result.innerHTML = `
    <p style="margin:0 0 14px; padding:12px 14px; background:rgba(59,130,246,.08); border:1px solid rgba(59,130,246,.35); border-radius:9px; color:#93c5fd; font-size:.78rem; line-height:1.6;">
      <i class="fa-solid fa-circle-check"></i> <b>실제 종가 데이터</b>(Yahoo Finance 일별 종가, 기준일 ${data.as_of})로 계산했습니다. GBM 가상 시뮬레이션은 <b>전략 백테스트</b> 화면을 참고하세요.
    </p>
    <img src="${data.image}" style="width:100%; border-radius:8px; margin-bottom:16px;"/>
    <div style="display:flex; flex-wrap:wrap; gap:12px; margin-bottom:16px;">
      <div class="metric-box"><div style="font-size:.7rem; color:#64748b;">현재 상태</div><div style="font-size:1rem; font-weight:700; color:${data.current_position.startsWith('매수') ? '#22c55e' : '#94a3b8'};">${data.current_position}</div></div>
      <div class="metric-box"><div style="font-size:.7rem; color:#64748b;">최근 신호 변경일</div><div style="font-size:1rem; font-weight:700; color:var(--text);">${data.last_signal_change_at || '-'}</div></div>
      <div class="metric-box"><div style="font-size:.7rem; color:#64748b;">거래횟수</div><div style="font-size:1rem; font-weight:700; color:var(--text);">${data.n_trades}회</div></div>
    </div>
    <div style="overflow-x:auto; margin-bottom:16px;">
      <table style="width:100%; min-width:420px; border-collapse:collapse; font-size:.8rem;">
        <thead><tr style="color:#64748b; text-align:left;"><th style="padding:6px 10px;">지표</th><th style="padding:6px 10px;">전략</th><th style="padding:6px 10px;">${data.benchmark_label}</th></tr></thead>
        <tbody style="color:var(--text);">
          ${metricRow('총수익률', pct(m.total_return, 1), pct(b.total_return, 1))}
          ${metricRow('CAGR', pct(m.cagr, 2), pct(b.cagr, 2))}
          ${metricRow('Sharpe', m.sharpe.toFixed(2), b.sharpe.toFixed(2))}
          ${metricRow('Sortino', m.sortino.toFixed(2), b.sortino.toFixed(2))}
          ${metricRow('MDD', pct(m.mdd, 1), pct(b.mdd, 1))}
          ${metricRow('승률', pct(m.win_rate, 1), pct(b.win_rate, 1))}
        </tbody>
      </table>
    </div>
    ${isAuthenticated() ? `
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <input id="sbt-save-name" type="text" placeholder="전략 이름 (예: 삼성전자 20/60 크로스오버)" maxlength="80" class="param-input" style="flex:1; min-width:220px;">
        <button class="run-btn" id="sbt-save">전략으로 저장</button>
      </div>
      <p id="sbt-save-status" style="margin:8px 0 0; font-size:.75rem; color:#94a3b8;"></p>
    ` : `<p style="color:#94a3b8; font-size:.78rem;">로그인하면 이 백테스트를 "내 전략"으로 저장할 수 있습니다.</p>`}
  `;

  const saveBtn = result.querySelector('#sbt-save');
  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const status = result.querySelector('#sbt-save-status');
      const name = result.querySelector('#sbt-save-name').value.trim();
      if (!name) { status.textContent = '전략 이름을 입력하세요.'; status.style.color = '#ef4444'; return; }
      saveBtn.disabled = true; status.textContent = '저장 중…'; status.style.color = '#94a3b8';
      try {
        await authRequest('/api/strategy', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: 'real_backtest',
            name,
            ticker: data.ticker,
            params: {
              fast_ma: req.fast_ma, slow_ma: req.slow_ma, period: req.period,
              transaction_cost_bps: req.transaction_cost_bps, slippage_bps: req.slippage_bps,
              benchmark_ticker: data.benchmark_ticker,
            },
            snapshot: {
              metrics: m, benchmark_metrics: b, benchmark_label: data.benchmark_label,
              n_trades: data.n_trades, current_position: data.current_position,
              last_signal_change_at: data.last_signal_change_at, as_of: data.as_of,
            },
          }),
        });
        status.textContent = '"내 전략"에 저장했습니다.'; status.style.color = '#22c55e';
      } catch (error) {
        status.textContent = error.message; status.style.color = '#ef4444';
      } finally {
        saveBtn.disabled = false;
      }
    });
  }
}

export function strategyBacktestView(container) {
  container.innerHTML = `
    <div style="margin-bottom:24px;">
      <h1 style="font-size:1.25rem; font-weight:700; color:var(--text); margin-bottom:6px;"><i class="fa-solid fa-chart-line"></i> 실제 가격 백테스트</h1>
      <p style="font-size:0.875rem; color:var(--text-muted); line-height:1.6;">
        Yahoo Finance 실제 일별 종가에 이동평균 크로스오버 전략을 적용합니다. 거래비용·슬리피지·벤치마크를 함께 검토할 수 있습니다.
      </p>
    </div>
    <div style="background:#12161f; border-radius:12px; padding:24px; border:1px solid #232938;">
      <div style="display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:16px; margin-bottom:20px;">
        <div><label class="param-label">종목 티커</label><input id="sbt-ticker" type="text" value="005930.KS" maxlength="20" class="param-input"/></div>
        <div><label class="param-label">단기 MA</label><input id="sbt-fast" type="number" value="20" min="5" max="60" class="param-input"/></div>
        <div><label class="param-label">장기 MA</label><input id="sbt-slow" type="number" value="60" min="20" max="200" class="param-input"/></div>
        <div><label class="param-label">기간</label>
          <select id="sbt-period" class="param-input">${PERIODS.map(([v, l]) => `<option value="${v}" ${v === '2y' ? 'selected' : ''}>${l}</option>`).join('')}</select>
        </div>
        <div><label class="param-label">거래비용 (bp)</label><input id="sbt-cost" type="number" value="10" min="0" max="500" class="param-input"/></div>
        <div><label class="param-label">슬리피지 (bp)</label><input id="sbt-slippage" type="number" value="5" min="0" max="500" class="param-input"/></div>
        <div><label class="param-label">벤치마크 (선택)</label><input id="sbt-benchmark" type="text" placeholder="비워두면 Buy & Hold" maxlength="20" class="param-input"/></div>
      </div>
      <button class="run-btn" id="sbt-run">▶ 백테스트 실행</button>
      <div id="sbt-result" style="margin-top:20px;"></div>
    </div>`;

  container.querySelector('#sbt-run').addEventListener('click', async () => {
    const btn = container.querySelector('#sbt-run');
    const result = container.querySelector('#sbt-result');
    const req = {
      ticker: container.querySelector('#sbt-ticker').value.trim(),
      fast_ma: +container.querySelector('#sbt-fast').value,
      slow_ma: +container.querySelector('#sbt-slow').value,
      period: container.querySelector('#sbt-period').value,
      transaction_cost_bps: +container.querySelector('#sbt-cost').value,
      slippage_bps: +container.querySelector('#sbt-slippage').value,
      benchmark_ticker: container.querySelector('#sbt-benchmark').value.trim(),
    };
    btn.disabled = true; btn.textContent = '실행 중...';
    result.innerHTML = '<p style="color:#94a3b8;">실제 가격 데이터로 백테스트 중...</p>';
    try {
      const data = await authRequest('/api/strategy/backtest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req),
      });
      renderResult(container, req, data);
    } catch (e) {
      result.innerHTML = `<p style="color:#ef4444;">오류: ${e.message}</p>`;
    } finally {
      btn.disabled = false; btn.textContent = '▶ 백테스트 실행';
    }
  });

  if (!isAuthenticated()) {
    container.insertAdjacentHTML('beforeend', `<p style="margin-top:12px; color:#94a3b8; font-size:.78rem;"><button type="button" id="sbt-login-hint" style="background:none;border:none;color:#f0b90b;cursor:pointer;padding:0;">로그인</button>하면 백테스트 결과를 "내 전략"으로 저장할 수 있습니다.</p>`);
    container.querySelector('#sbt-login-hint').addEventListener('click', openAuthModal);
  }
}
