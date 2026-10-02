// 전략 추천 (EINT-017): 3개 아이템의 오늘 추천 종목(아이템별 최대 5개)과 내 보유 종목 매도 신호를 한 화면에 보여 준다.
// EINT-020: 추천 카드에서 바로 매수 기록·관망·제외를 남기고, 엔진 실행 이상은 상단 배너로 알린다.
// 기록은 사용자가 직접 입력한 값만 저장한다(자동 주문·자동 모의체결 없음). 매도는 "기록·연구" 화면의 보유 현황에서 한다.
import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';
import { manualRequest } from './kqsManualRecords.js';
import { fillSectorBadges, sectorSlot } from '../utils/sectorBadges.js';
import { liveSlot, startLiveQuotes } from '../utils/liveQuotes.js';

const esc = (value) => String(value ?? '-').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const kst = (value) => {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? esc(value) : esc(date.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }));
};
const price = (value) => (Number.isFinite(Number(value)) ? Number(value).toLocaleString('ko-KR', { maximumFractionDigits: 2 }) : '-');

const STATE_LABEL = {
  exit_signal: ['매도 신호', 'is-sell'], hold: ['보유 유지', 'is-hold'], no_item: ['아이템 없음', 'is-muted'],
  not_monitored: ['모니터 대상 아님', 'is-muted'], unavailable: ['판단 불가', 'is-muted'],
};
const ITEM_SHORT = { swing: 'Bollinger-RSI', long_term: 'RSI-OBV', pullback_v4: 'Pullback v4' };
export const DECISION_LABEL = { buy: ['매수', 'is-hold'], hold: ['관망', 'is-muted'], exclude: ['제외', 'is-muted'],
  sell: ['매도', 'is-sell'], review_again: ['재검토', 'is-muted'] };
const todayKst = () => new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);

function decisionBadges(decisions) {
  if (!decisions || !decisions.length) return '';
  return `<p class="sb-note" data-sb-decided>내 기록: ${decisions.map((row) => {
    const [label, tone] = DECISION_LABEL[row.decision] || [row.decision, 'is-muted'];
    const detail = row.decision === 'buy' && row.quantity ? ` ${esc(row.quantity)}주 @${price(row.fill_price)}` : '';
    return `<span class="sb-badge ${tone}">${esc(label)}${detail}</span>`;
  }).join(' ')}</p>`;
}

export function decisionForm(pick) {
  return `<form class="sb-note" data-sb-decision data-signal-id="${esc(pick.signal_id)}" style="display:grid; gap:6px;">
    <div style="display:flex; gap:6px; flex-wrap:wrap;">
      <button type="button" class="run-btn" data-sb-open-buy>매수 기록</button>
      <button type="button" data-sb-quick="hold">관망</button>
      <button type="button" data-sb-quick="exclude">제외</button>
    </div>
    <div data-sb-buy-fields hidden style="grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px;">
      <label>수량(주)<input type="number" name="quantity" min="0" step="any" required style="width:100%"></label>
      <label>체결가(원)<input type="number" name="fill_price" min="0" step="any" required style="width:100%"></label>
      <label>거래일<input type="date" name="trade_date" value="${todayKst()}" style="width:100%"></label>
      <label>수수료(원, 선택)<input type="number" name="fee_krw" min="0" step="any" value="0" style="width:100%"></label>
      <label style="grid-column:1/-1">메모(선택)<input type="text" name="memo" maxlength="1000" style="width:100%"></label>
      <button type="submit" class="run-btn" style="grid-column:1/-1">매수 기록 저장</button>
    </div>
    <span data-sb-decision-status role="status"></span>
  </form>`;
}

function healthBanner(health) {
  if (!health || health.level === 'ok' || !(health.alerts || []).length) return '';
  const color = health.level === 'error' ? '#f87171' : '#fbbf24';
  return `<section class="sb-sells" data-sb-health="${esc(health.level)}" style="margin-bottom:14px; border-color:${color};">
    <header><h2 style="color:${color}"><i class="fa-solid fa-triangle-exclamation"></i> 엔진 상태 확인 필요</h2></header>
    <ul class="sb-reasons" style="padding-left:18px;">${health.alerts.map((alert) => `<li><b>${esc(alert.title)}</b> — ${esc(alert.message)}</li>`).join('')}</ul>
  </section>`;
}

function pickRow(pick, decisions) {
  return `<li class="sb-pick">
    <details>
      <summary><span class="sb-rank">${esc(pick.rank)}</span><span class="sb-name"><strong>${esc(pick.name)}</strong><small>${esc(pick.symbol)} · ${esc(pick.market)}</small>${sectorSlot(pick.symbol)}</span><span class="sb-close">${price(pick.close)}<small>기준일 종가</small>${liveSlot(pick.symbol, pick.market)}</span></summary>
      <ul class="sb-reasons">${(pick.reasons || []).map((reason) => `<li>${esc(reason)}</li>`).join('')}</ul>
      ${pick.exit_reference ? `<p class="sb-note"><b>청산 기준</b> ${esc(pick.exit_reference)}</p>` : ''}
      ${decisionBadges(decisions[pick.signal_id])}
      ${pick.signal_id ? decisionForm(pick) : ''}
    </details>
  </li>`;
}

function itemCard(item, decisions) {
  const body = !item.published
    ? '<p class="sb-empty">이 아이템은 엔진 v2 게시 후부터 표시됩니다.</p>'
    : item.picks.length
      ? `<ol class="sb-picks">${item.picks.map((pick) => pickRow(pick, decisions)).join('')}</ol>`
      : '<p class="sb-empty">오늘은 이 아이템의 조건을 만족한 종목이 없습니다.</p>';
  return `<article class="sb-item" data-item="${esc(item.item)}">
    <header><span class="sb-label">${esc(item.label)}</span><h2>${esc(item.title)}</h2>
      <span class="sb-count">${item.published ? `${item.picks.length} / ${item.max_picks}` : '-'}</span></header>
    <p class="sb-summary">${esc(item.summary)}</p>
    ${body}
    <footer><span>정렬: ${esc(item.order)}</span><span>청산: ${esc(item.exit)}</span></footer>
  </article>`;
}

function sellRow(row) {
  const [label, tone] = STATE_LABEL[row.state] || [row.state, 'is-muted'];
  const levels = row.item === 'pullback_v4' && row.state !== 'no_item' && row.stop_price !== undefined
    ? `손절 ${price(row.stop_price)} · 트레일링 ${price(row.trailing_level)} · 보유 ${row.holding_days ?? '15일 초과'}/${row.holding_limit}일${row.market_regime_off ? ' · 시장 50일선 이탈' : ''}`
    : (row.detail || '');
  return `<tr class="${row.state === 'exit_signal' ? 'is-alert' : ''}">
    <td><strong>${esc(row.name || row.symbol)}</strong><small>${esc(row.symbol)}</small></td>
    <td>${esc(ITEM_SHORT[row.item] || '-')}</td>
    <td>${esc(row.bought_on)}<small>${price(row.fill_price)}원 · ${esc(row.remaining_quantity)}주</small></td>
    <td>${row.close !== undefined ? price(row.close) : '-'}</td>
    <td><span class="sb-badge ${tone}">${esc(label)}</span>${row.reason_label ? `<small>${esc(row.reason_label)}</small>` : ''}</td>
    <td class="sb-levels">${esc(levels)}</td>
  </tr>`;
}

export function boardHtml(data) {
  const meta = data.available
    ? `데이터 기준일 <b>${esc(data.as_of)}</b> · 만료 ${kst(data.expires_at)} · 엔진 ${esc(data.package_version)}`
    : `<span class="sb-error">검증된 추천을 사용할 수 없습니다. ${esc(data.detail || '')}</span>`;
  const sells = data.sell_signals || [];
  const decisions = data.my_decisions || {};
  const health = data.health;
  const engineLine = health && health.level === 'ok'
    ? ` · 엔진 정상(최근 실행 ${kst(health.last_run_finished_at)})` : '';
  const alerts = sells.filter((row) => row.state === 'exit_signal').length;
  return `
    <section class="sb-head">
      <div><h1><i class="fa-solid fa-bullseye"></i> 전략 추천</h1><p>${meta}${engineLine}</p></div>
      <div class="sb-actions"><button type="button" class="run-btn" data-sb-refresh><i class="fa-solid fa-rotate"></i> 새로고침</button>
        <button type="button" data-sb-record><i class="fa-solid fa-pen-to-square"></i> 매수·매도 기록하기</button></div>
    </section>
    ${healthBanner(health)}
    <section class="sb-grid">${(data.items || []).map((item) => itemCard(item, decisions)).join('')}</section>
    <section class="sb-sells">
      <header><h2>내 보유 종목 매도 신호</h2><span class="sb-count ${alerts ? 'is-alert' : ''}">${alerts}건</span></header>
      ${sells.length ? `<div class="sb-table"><table><thead><tr><th>종목</th><th>아이템</th><th>매수일·가격</th><th>기준 종가</th><th>신호</th><th>세부</th></tr></thead>
        <tbody>${sells.map(sellRow).join('')}</tbody></table></div>`
        : '<p class="sb-empty">기록된 보유 종목이 없습니다. 매수하면 "매수·매도 기록하기"에서 기록하세요.</p>'}
      <p class="sb-note">매도 신호는 그 종목을 추천한 아이템의 청산 기준입니다. Pullback v4의 손절·트레일링은 다음 날 시가 대신 기준일 종가로 미리 알려 줍니다.</p>
    </section>
    <p class="sb-disclaimer">${esc(data.disclaimer)}</p>`;
}

export function bindDecisionForm(form, reload) {
  const status = form.querySelector('[data-sb-decision-status]');
  const fields = form.querySelector('[data-sb-buy-fields]');
  const submit = async (body) => {
    status.textContent = '기록하는 중...';
    form.querySelectorAll('button').forEach((button) => { button.disabled = true; });
    try {
      await authRequest('/api/kqs/decisions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(manualRequest(form, { signal_id: form.dataset.signalId, ...body })),
      });
      status.textContent = '기록했습니다.';
      reload();
    } catch (error) {
      status.textContent = error.message;
      form.querySelectorAll('button').forEach((button) => { button.disabled = false; });
    }
  };
  form.querySelector('[data-sb-open-buy]').addEventListener('click', () => {
    fields.hidden = !fields.hidden;
    fields.style.display = fields.hidden ? '' : 'grid';
  });
  form.querySelectorAll('[data-sb-quick]').forEach((button) => button.addEventListener('click', () => {
    const label = button.textContent.trim();
    if (window.confirm(`이 추천을 "${label}"(으)로 기록할까요?`)) submit({ decision: button.dataset.sbQuick });
  }));
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const quantity = Number(form.elements.quantity.value);
    const fillPrice = Number(form.elements.fill_price.value);
    if (!(quantity > 0) || !(fillPrice > 0)) { status.textContent = '수량과 체결가를 입력하세요.'; return; }
    submit({
      decision: 'buy', quantity, fill_price: fillPrice, trade_date: form.elements.trade_date.value || null,
      fee_krw: Number(form.elements.fee_krw.value) || 0, memo: form.elements.memo.value,
    });
  });
}

export function strategyBoardView(container) {
  if (!isAuthenticated()) {
    container.innerHTML = `<section class="sb-login"><h1>전략 추천을 보려면 로그인하세요</h1>
      <p>3개 전략의 오늘 추천 종목과 내 보유 종목의 매도 신호를 한 화면에서 확인합니다.</p>
      <button type="button" class="run-btn" data-sb-login>로그인하고 시작하기</button></section>`;
    container.querySelector('[data-sb-login]').addEventListener('click', openAuthModal);
    return;
  }
  const load = async () => {
    container.innerHTML = '<p class="sb-empty">전략 추천을 불러오는 중…</p>';
    try {
      container.innerHTML = boardHtml(await authRequest('/api/kqs/strategy-board'));
    } catch (error) {
      container.innerHTML = `<p class="sb-error">전략 추천을 불러오지 못했습니다. ${esc(error.message)}</p>`;
    }
    container.querySelector('[data-sb-refresh]')?.addEventListener('click', load);
    container.querySelector('[data-sb-record]')?.addEventListener('click', () => window.navigate('my-strategies'));
    container.querySelectorAll('[data-sb-decision]').forEach((form) => bindDecisionForm(form, load));
    fillSectorBadges(container);
    startLiveQuotes(container);
  };
  load();
}
