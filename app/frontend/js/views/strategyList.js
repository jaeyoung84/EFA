import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';

const TYPE_BADGE = {
  real_backtest: { label: '실제 가격', color: '#22c55e' },
  virtual_backtest: { label: '가상 시뮬레이션', color: '#f59e0b' },
  lean: { label: 'LEAN 사전 실행', color: '#f0b90b' },
};

const pct = (value, digits = 2) => (value == null ? '-' : `${(Number(value) * 100).toFixed(digits)}%`);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));

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
  return `<tr>
    <td style="padding:8px 10px;">${f.filled_at ? f.filled_at.slice(0, 16).replace('T', ' ') : '-'}</td>
    <td style="padding:8px 10px;">${escapeHtml(f.ticker)}</td>
    <td style="padding:8px 10px;">${f.direction === 'buy' ? '매수' : '매도'}</td>
    <td style="padding:8px 10px;">${f.quantity}</td>
    <td style="padding:8px 10px;">${Number(f.fill_price).toLocaleString('ko-KR')}</td>
  </tr>`;
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
    <div id="stg-detail"></div>
    <div style="margin-top:28px;">
      <h2 style="font-size:1rem; color:var(--text); margin-bottom:10px;">전체 모의 체결 내역</h2>
      <p style="margin:0 0 10px; color:#64748b; font-size:.72rem;">주문 제안을 승인하면 여기에 기록됩니다. 실제 보유자산(포트폴리오 → 내 보유자산)과는 별개의 모의 기록입니다.</p>
      <div id="stg-fills" style="background:#12161f; border:1px solid #232938; border-radius:10px; padding:14px;"><p style="color:#94a3b8; font-size:.8rem; margin:0;">불러오는 중...</p></div>
    </div>`;

  const listEl = container.querySelector('#stg-list');
  const detailEl = container.querySelector('#stg-detail');
  const fillsEl = container.querySelector('#stg-fills');

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
            await authRequest(`/api/strategy/proposals/${row.dataset.proposalId}/approve`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quantity: qty }),
            });
            await loadProposals(strategyId, root);
            await loadFills();
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
        ${metricsHtml}
        ${statsHtml}
        ${item.type === 'real_backtest' ? `
          <div style="border-top:1px solid #232938; padding-top:14px; margin-top:4px;">
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
        const proposalsRoot = detailEl.querySelector('#stg-proposals');
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
  loadFills();
}
