import { api } from '../api.js';
import { authRequest, isAuthenticated } from '../auth.js';
import { cloudResourceCard } from './cloudAiResources.js';

export function backtestView(container) {
  let lastReq = null;
  container.innerHTML = `
    <div style="margin-bottom:24px;">
      <h1 style="font-size:1.25rem; font-weight:700; color:var(--text); margin-bottom:6px;"><i class="fa-solid fa-clock-rotate-left"></i> 백테스트 엔진</h1>
      <p style="font-size:0.875rem; color:var(--text-muted); line-height:1.6;">
        GBM으로 시뮬레이션된 <b>가상 가격</b>에 이동평균 크로스오버 전략을 적용하고 성과를 분석합니다. 실제 종가로 백테스트하려면 좌측 메뉴의 "실제 가격 백테스트"를 사용하세요.
      </p>
    </div>
    <div style="background:#12161f; border-radius:12px; padding:24px; border:1px solid #232938;">
      <div style="display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:16px; margin-bottom:20px;">
        <div>
          <label class="param-label">단기 MA</label>
          <input id="bt-short" type="number" value="20" min="5" max="60" class="param-input"/>
        </div>
        <div>
          <label class="param-label">장기 MA</label>
          <input id="bt-long" type="number" value="60" min="20" max="200" class="param-input"/>
        </div>
        <div>
          <label class="param-label">거래일 수</label>
          <input id="bt-days" type="number" value="1260" min="252" max="5040" class="param-input"/>
        </div>
        <div>
          <label class="param-label">변동성 (σ)</label>
          <input id="bt-vol" type="number" value="0.2" min="0.05" max="0.8" step="0.05" class="param-input"/>
        </div>
      </div>
      <button class="run-btn" id="bt-run">▶ 백테스트 실행</button>
      <div id="bt-result" style="margin-top:20px;"></div>
    </div>
    ${cloudResourceCard('backtest')}`;

  container.querySelector('#bt-run').addEventListener('click', async () => {
    const btn = container.querySelector('#bt-run');
    const result = container.querySelector('#bt-result');
    btn.disabled = true; btn.textContent = '실행 중...';
    result.innerHTML = '<p style="color:#94a3b8;">백테스트 실행 중...</p>';
    try {
      lastReq = {
        fast_ma:    +container.querySelector('#bt-short').value,
        slow_ma:    +container.querySelector('#bt-long').value,
        n_days:     +container.querySelector('#bt-days').value,
        volatility: +container.querySelector('#bt-vol').value,
      };
      const data = await api.backtest(lastReq);
      let html = '';
      if (data.image) html += `<img src="${data.image}" style="width:100%; border-radius:8px; margin-bottom:16px;"/>`;
      if (data.metrics) {
        const m = data.metrics;
        html += `<div style="display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:12px; margin-bottom:16px;">
          ${Object.entries(m).map(([k, v]) => `
            <div class="metric-box">
              <div style="font-size:0.7rem; color:#64748b; text-transform:uppercase; margin-bottom:4px; word-break:break-word;">${k.replace(/_/g,' ')}</div>
              <div style="font-size:1rem; font-weight:700; color:${String(v).startsWith('-') ? 'var(--down)' : 'var(--up)'};">${typeof v === 'number' ? v.toFixed(2) : v}</div>
            </div>`).join('')}
        </div>`;
        html += isAuthenticated()
          ? `<div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
              <input id="bt-save-name" type="text" placeholder="전략 이름 (가상 시뮬레이션)" maxlength="80" class="param-input" style="flex:1; min-width:220px;">
              <button class="run-btn" id="bt-save" style="padding:8px 12px; font-size:.75rem;">전략으로 저장</button>
            </div>
            <p id="bt-save-status" style="margin:8px 0 0; font-size:.75rem; color:#94a3b8;"></p>`
          : `<p style="color:#94a3b8; font-size:.78rem;">로그인하면 이 결과를 "내 전략"으로 저장할 수 있습니다.</p>`;
      }
      result.innerHTML = html;

      const saveBtn = result.querySelector('#bt-save');
      if (saveBtn) {
        saveBtn.addEventListener('click', async () => {
          const status = result.querySelector('#bt-save-status');
          const name = result.querySelector('#bt-save-name').value.trim();
          if (!name) { status.textContent = '전략 이름을 입력하세요.'; status.style.color = '#ef4444'; return; }
          saveBtn.disabled = true; status.textContent = '저장 중…'; status.style.color = '#94a3b8';
          try {
            await authRequest('/api/strategy', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ type: 'virtual_backtest', name, params: lastReq, snapshot: { metrics: data.metrics } }),
            });
            status.textContent = '"내 전략"에 저장했습니다.'; status.style.color = '#22c55e';
          } catch (error) {
            status.textContent = error.message; status.style.color = '#ef4444';
          } finally {
            saveBtn.disabled = false;
          }
        });
      }
    } catch (e) {
      result.innerHTML = `<p style="color:#ef4444;">오류: ${e.message}</p>`;
    } finally {
      btn.disabled = false; btn.textContent = '▶ 백테스트 실행';
    }
  });
}
