import { api } from '../api.js';
import { dataSourceNote } from './dataSourceNote.js';

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmt(value, suffix = '') {
  return value === null || value === undefined ? '-' : `${value}${suffix}`;
}

// ── 탭 공통 유틸 ─────────────────────────────────────────────────────────────
function tabShell(tabs) {
  return `
    <div style="margin-bottom:24px;">
      <h1 style="font-size:1.25rem; font-weight:700; color:var(--text); margin-bottom:6px;"><i class="fa-solid fa-industry"></i> 산업 비교</h1>
      <p style="font-size:0.875rem; color:#94a3b8; line-height:1.6;">
        같은 업종 기업의 재무 지표와 섹터 ETF 수익률을 실제 시세로 비교합니다.
      </p>
    </div>
    ${dataSourceNote('real', '두 탭 모두 Yahoo Finance 실제 데이터를 사용합니다.')}
    <div style="display:flex; flex-wrap:wrap; gap:4px; margin-bottom:20px; border-bottom:1px solid #334155; padding-bottom:0;">
      ${tabs.map((t, i) => `
        <button class="ia-tab" data-tab="${i}"
          style="padding:9px 18px; border:none; border-radius:8px 8px 0 0; cursor:pointer;
                 font-size:0.825rem; font-weight:600; transition:all 0.15s;
                 background:${i===0?'#2563eb':'#1e293b'};
                 color:${i===0?'#fff':'#94a3b8'};">
          ${t}
        </button>`).join('')}
    </div>
    <div id="ia-content"></div>`;
}

function activateTab(container, idx, renders) {
  container.querySelectorAll('.ia-tab').forEach((btn, i) => {
    btn.style.background = i === idx ? '#2563eb' : '#1e293b';
    btn.style.color      = i === idx ? '#fff'    : '#94a3b8';
  });
  renders[idx](container.querySelector('#ia-content'));
}

// ── 메인 진입점 ──────────────────────────────────────────────────────────────
export function industryAnalysisView(container) {
  const TABS = ['① 동종 기업 비교', '② 섹터 주가 비교'];
  container.innerHTML = tabShell(TABS);
  const renders = [renderPeer, renderSector];
  container.querySelectorAll('.ia-tab').forEach((btn, i) => {
    btn.addEventListener('click', () => activateTab(container, i, renders));
  });
  renders[0](container.querySelector('#ia-content'));
}


// ── Tab 1: 동종 기업 비교 ─────────────────────────────────────────────
function renderPeer(el) {
  el.innerHTML = `
    <div style="background:#1e293b; border-radius:12px; padding:24px; border:1px solid #334155;">
      <div style="margin-bottom:14px;">
        <label class="param-label">기업명,티커 한 줄씩 입력</label>
        <textarea id="peer-input" rows="5" class="param-input" style="resize:vertical;" placeholder="예) 기업명,티커 형식으로 한 줄에 하나씩 (국내 종목은 .KS / .KQ)"></textarea>
      </div>
      <button class="run-btn" id="peer-run">Peer 재무표 생성</button>
      <div id="peer-result" style="margin-top:20px;"></div>
    </div>`;

  el.querySelector('#peer-run').addEventListener('click', async () => {
    const btn = el.querySelector('#peer-run');
    const result = el.querySelector('#peer-result');
    const tickers = {};
    el.querySelector('#peer-input').value.split('\n').forEach((line) => {
      const [name, ticker] = line.split(',').map((x) => x?.trim());
      if (name && ticker) tickers[name] = ticker;
    });
    if (!Object.keys(tickers).length) {
      result.innerHTML = '<p style="color:#ef4444;">기업명,티커 형식으로 1개 이상 입력하세요.</p>';
      return;
    }

    btn.disabled = true; btn.textContent = '수신 중...';
    result.innerHTML = '<p style="color:#94a3b8;">Yahoo Finance 재무 데이터 수신 중...</p>';
    try {
      const data = await api.industryPeer({ tickers });
      result.innerHTML = `
        <div style="overflow-x:auto;">
          <table style="width:100%;border-collapse:collapse;font-size:0.8rem;">
            <thead><tr style="background:#0f172a;">
              ${['기업','티커','시가총액(억)','매출성장률','영업이익률','PER','PBR','부채비율','ROE'].map((h) => `
                <th style="padding:9px 10px;text-align:${h === '기업' || h === '티커' ? 'left' : 'right'};color:#94a3b8;font-size:0.7rem;">${h}</th>`).join('')}
            </tr></thead>
            <tbody>
              ${data.rows.map((r) => `
                <tr style="border-bottom:1px solid #334155;">
                  <td style="padding:9px 10px;color:#e2e8f0;font-weight:700;">${escapeHtml(r.company)}</td>
                  <td style="padding:9px 10px;color:#94a3b8;">${escapeHtml(r.ticker)}</td>
                  ${r.error ? `
                    <td colspan="7" style="padding:9px 10px;color:#ef4444;">${escapeHtml(r.error)}</td>
                  ` : `
                    <td style="padding:9px 10px;text-align:right;color:#cbd5e1;">${fmt(r.market_cap_krw_100m)}</td>
                    <td style="padding:9px 10px;text-align:right;color:${(r.revenue_growth_pct ?? 0) >= 0 ? '#22c55e' : '#ef4444'};">${fmt(r.revenue_growth_pct, '%')}</td>
                    <td style="padding:9px 10px;text-align:right;color:#cbd5e1;">${fmt(r.operating_margin_pct, '%')}</td>
                    <td style="padding:9px 10px;text-align:right;color:#cbd5e1;">${fmt(r.per)}</td>
                    <td style="padding:9px 10px;text-align:right;color:#cbd5e1;">${fmt(r.pbr)}</td>
                    <td style="padding:9px 10px;text-align:right;color:#cbd5e1;">${fmt(r.debt_to_equity_pct, '%')}</td>
                    <td style="padding:9px 10px;text-align:right;color:#cbd5e1;">${fmt(r.roe_pct, '%')}</td>
                  `}
                </tr>`).join('')}
            </tbody>
          </table>
        </div>
        <div style="margin-top:14px; display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px;">
          <div class="metric-box">
            <div style="font-size:0.7rem; color:#64748b; margin-bottom:4px;">수익성 우위 후보</div>
            <div style="font-size:1rem; font-weight:700; color:#3b82f6;">${escapeHtml(data.leader || '-')}</div>
          </div>
          ${data.notes.map((note) => `
            <div style="background:#0f172a; border-radius:8px; padding:12px; color:#94a3b8; font-size:0.78rem; line-height:1.5;">${escapeHtml(note)}</div>
          `).join('')}
        </div>`;
    } catch(e) {
      result.innerHTML = `<p style="color:#ef4444;">오류: ${escapeHtml(e.message)}</p>`;
    } finally {
      btn.disabled = false; btn.textContent = 'Peer 재무표 생성';
    }
  });
}



// ── Tab 2: 섹터 주가 비교 ────────────────────────────────────────────────────
const SECTOR_OPTS = [
  { v:'SOXX', l:'반도체 (SOXX)' }, { v:'XLE', l:'에너지 (XLE)' },
  { v:'XLF', l:'금융 (XLF)' },    { v:'XLV', l:'헬스케어 (XLV)' },
  { v:'XLK', l:'기술 (XLK)' },    { v:'XLI', l:'산업재 (XLI)' },
  { v:'XLY', l:'소비재경기 (XLY)'},{ v:'XLP', l:'소비재필수 (XLP)'},
  { v:'XLB', l:'소재 (XLB)' },    { v:'XLRE',l:'부동산 (XLRE)'},
  { v:'XLU', l:'유틸리티 (XLU)' },{ v:'IBB', l:'바이오 (IBB)' },
];
const DEFAULT_SECTORS = ['SOXX','XLE','XLF','XLV','XLK','XLI'];
const PERIODS_SEC = [
  {v:'1mo',l:'1개월'},{v:'3mo',l:'3개월'},{v:'6mo',l:'6개월'},
  {v:'1y',l:'1년'},{v:'2y',l:'2년'},{v:'5y',l:'5년'},
];

function renderSector(el) {
  el.innerHTML = `
    <div style="background:#1e293b; border-radius:12px; padding:24px; border:1px solid #334155;">
      <div style="margin-bottom:16px;">
        <label class="param-label" style="margin-bottom:8px; display:block;">섹터 ETF 선택</label>
        <div style="display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:8px;" id="sec-grid">
          ${SECTOR_OPTS.map(t => `
            <label style="display:flex; align-items:center; gap:8px; padding:8px 12px;
                   background:#0f172a; border-radius:8px; cursor:pointer;
                   border:1px solid #334155; font-size:0.8rem; color:#94a3b8;">
              <input type="checkbox" value="${t.v}" ${DEFAULT_SECTORS.includes(t.v)?'checked':''}
                     style="accent-color:#3b82f6;"/>
              ${t.l}
            </label>`).join('')}
        </div>
      </div>
      <div style="display:flex; align-items:center; gap:10px; margin-bottom:20px; flex-wrap:wrap;">
        <label class="param-label" style="margin:0;">기간:</label>
        <div style="display:flex; gap:6px; flex-wrap:wrap;" id="sec-period">
          ${PERIODS_SEC.map(p => `
            <button data-p="${p.v}" class="sec-pbtn"
              style="padding:5px 12px; border-radius:6px; border:1px solid #334155; cursor:pointer;
                     font-size:0.8rem; background:${p.v==='1y'?'#2563eb':'#0f172a'};
                     color:${p.v==='1y'?'#fff':'#94a3b8'};">${p.l}</button>`).join('')}
        </div>
      </div>
      <button class="run-btn" id="sec-run">▶ 비교 분석</button>
      <div id="sec-result" style="margin-top:20px;"></div>
    </div>`;

  let selPeriod = '1y';
  el.querySelectorAll('.sec-pbtn').forEach(b => {
    b.addEventListener('click', () => {
      selPeriod = b.dataset.p;
      el.querySelectorAll('.sec-pbtn').forEach(x => {
        x.style.background = x===b?'#2563eb':'#0f172a';
        x.style.color      = x===b?'#fff':'#94a3b8';
      });
    });
  });

  el.querySelector('#sec-run').addEventListener('click', async () => {
    const btn    = el.querySelector('#sec-run');
    const result = el.querySelector('#sec-result');
    const tickers = [...el.querySelectorAll('#sec-grid input:checked')].map(x => x.value);
    if (!tickers.length) { result.innerHTML='<p style="color:#ef4444;">최소 1개 선택하세요.</p>'; return; }
    btn.disabled=true; btn.textContent='수신 중...';
    result.innerHTML='<p style="color:#94a3b8;">Yahoo Finance 데이터 수신 중... (10~20초)</p>';
    try {
      const data = await api.industrySector({ tickers, period: selPeriod });
      let html = '';
      if (data.image) html += `<img src="${data.image}" style="width:100%; border-radius:8px; margin-bottom:16px;"/>`;
      if (data.summary) {
        const sorted = Object.entries(data.summary).sort((a,b)=>b[1].return_pct-a[1].return_pct);
        html += `<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:0.825rem;">
          <thead><tr style="background:#0f172a;">
            <th style="padding:8px 12px;text-align:left;color:#64748b;font-size:0.7rem;">섹터</th>
            <th style="padding:8px 12px;text-align:right;color:#64748b;font-size:0.7rem;">수익률</th>
            <th style="padding:8px 12px;text-align:right;color:#64748b;font-size:0.7rem;">연환산 변동성</th>
          </tr></thead><tbody>
          ${sorted.map(([n,s])=>`<tr style="border-bottom:1px solid #334155;">
            <td style="padding:8px 12px;color:#e2e8f0;">${n}</td>
            <td style="padding:8px 12px;text-align:right;font-weight:700;
                color:${s.return_pct>=0?'#22c55e':'#ef4444'};">${s.return_pct>=0?'▲':'▼'}${Math.abs(s.return_pct).toFixed(2)}%</td>
            <td style="padding:8px 12px;text-align:right;color:#94a3b8;">${s.annual_vol.toFixed(1)}%</td>
          </tr>`).join('')}
          </tbody></table></div>`;
      }
      result.innerHTML = html;
    } catch(e) {
      result.innerHTML=`<p style="color:#ef4444;">오류: ${e.message}</p>`;
    } finally {
      btn.disabled=false; btn.textContent='▶ 비교 분석';
    }
  });
}
