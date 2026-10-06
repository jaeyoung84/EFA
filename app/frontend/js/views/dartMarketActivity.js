import { api } from '../api.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));

const LABELS = {
  treasury_decision: '자사주 취득 결정',
  treasury_trust: '자사주 신탁계약',
  treasury_result: '자사주 취득 결과',
  insider_increase: '임원·주요주주 증가',
  insider_decrease: '임원·주요주주 감소',
  insider_change: '임원·주요주주 변동',
  major_increase: '5% 이상 보유 증가',
  major_decrease: '5% 이상 보유 감소',
  major_change: '5% 이상 보유 변동',
};

const GROUPS = {
  all: () => true,
  treasury: (kind) => kind.startsWith('treasury'),
  insider_increase: (kind) => kind === 'insider_increase',
  insider_decrease: (kind) => kind === 'insider_decrease',
  major: (kind) => kind.startsWith('major'),
};

// 시장 구분·날짜별 구분(2026-10-06, 사용자 승인): PC는 코스피·코스닥 두 칸, 모바일은 시장 탭.
const BOARD_MARKETS = [['KOSPI', '코스피'], ['KOSDAQ', '코스닥'], ['기타', '코넥스·기타']];
export const marketOf = (item) => (item.market === 'KOSPI' || item.market === 'KOSDAQ' ? item.market : '기타');
const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

export function formatDisclosureDate(raw) {
  const text = String(raw || '').replace(/-/g, '');
  if (!/^\d{8}$/.test(text)) return '접수일 미확인';
  const [y, m, d] = [text.slice(0, 4), text.slice(4, 6), text.slice(6, 8)];
  const weekday = WEEKDAYS[new Date(Date.UTC(+y, +m - 1, +d)).getUTCDay()];
  return `${y}.${m}.${d} (${weekday})`;
}

/** 최신 접수일부터 날짜별로 묶는다(같은 날 안에서는 접수번호 역순). */
export function groupByDate(items) {
  const sorted = [...items].sort((a, b) => String(b.rcept_date || '').localeCompare(String(a.rcept_date || ''))
    || String(b.rcept_no || '').localeCompare(String(a.rcept_no || '')));
  const groups = [];
  for (const item of sorted) {
    const key = String(item.rcept_date || '');
    if (!groups.length || groups[groups.length - 1].key !== key) groups.push({ key, label: formatDisclosureDate(key), items: [] });
    groups[groups.length - 1].items.push(item);
  }
  return groups;
}

const number = (value) => Number.isFinite(Number(value)) ? Number(value).toLocaleString() : '-';
const signed = (value, suffix = '') => Number.isFinite(Number(value))
  ? `${Number(value) > 0 ? '+' : ''}${Number(value).toLocaleString()}${suffix}` : '-';
const present = (value) => value !== null && value !== undefined && value !== '';

/** 게시판 한 줄: 유형·회사·원문 / 보고자 / 보유 변동(지분 공시) 또는 공시명(자사주). */
export function itemHtml(item) {
  const kind = item.kind || '';
  const who = [item.reporter, item.relationship, item.position].filter((v) => v && String(v).trim() !== '-').map(esc).join(' · ');
  const holding = present(item.holding_count) || present(item.holding_rate)
    ? `<p class="dart-item-nums">보유 <b>${number(item.holding_count)}주</b> (${signed(item.change_count)}) · 비율 <b>${Number.isFinite(Number(item.holding_rate)) ? `${Number(item.holding_rate).toLocaleString()}%` : '-'}</b> (${signed(item.change_rate, '%p')})</p>`
    : `<p class="dart-item-nums">${esc(item.report_name || '')}</p>`;
  const link = item.dart_url
    ? `<a class="dart-activity-link" href="${esc(item.dart_url)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(item.corp_name)} DART 원문">원문 <i class="fa-solid fa-arrow-up-right-from-square"></i></a>` : '';
  return `<article class="dart-item" data-kind="${esc(kind)}">
    <div class="dart-item-top"><span class="dart-activity-kind ${esc(kind)}">${esc(LABELS[kind] || '지분 변동')}</span>
      <strong>${esc(item.corp_name)}</strong><small>${esc(item.stock_code || '')}</small>${link}</div>
    ${who ? `<p class="dart-item-who">${who}</p>` : ''}
    ${holding}
    ${item.report_reason ? `<p class="dart-item-reason">${esc(item.report_reason)}</p>` : ''}
  </article>`;
}

export function columnHtml(items) {
  if (!items.length) return '<p class="dart-activity-empty">조건에 맞는 공시가 없습니다.</p>';
  return groupByDate(items).map((group) => `<h4 class="dart-board-date"><i class="fa-regular fa-calendar"></i> ${esc(group.label)} <span>${group.items.length.toLocaleString()}건</span></h4>${group.items.map(itemHtml).join('')}`).join('');
}

export function dartMarketActivityView(container) {
// Codex 수정: DART-001 (2026-10-06), 최근 7일 공시 안내와 기존 표시본 보존.
  container.innerHTML = `
    <section class="dart-activity-page">
      <header class="dart-activity-head">
        <div>
          <p class="dart-activity-kicker">READ-ONLY · OPEN DART</p>
          <h1><i class="fa-solid fa-building-shield"></i> 자사주·내부자 지분 변동</h1>
          <p>자사주 취득과 법정 신고 대상 임원·주요주주·5% 이상 보유자의 공시를 한 화면에 모읍니다.</p>
        </div>
        <div class="dart-activity-stamp" id="dart-activity-stamp">수집 기록 조회 중</div>
      </header>
      <section class="dart-activity-guide">
        <i class="fa-solid fa-circle-info" aria-hidden="true"></i>
        <p><strong>최근 7일 공시 · 평일 20:00 KST 수집</strong> · 보유수량 증가는 장내 매수를 뜻한다고 단정할 수 없습니다. 스톡옵션 행사·증여·전환 등은 DART 원문을 확인하세요.</p>
      </section>
      <div class="dart-activity-summary" id="dart-activity-summary"></div>
      <div class="dart-activity-toolbar" aria-label="공시 유형 필터">
        <div class="dart-activity-filters">
          <button class="active" data-dart-filter="all">전체</button>
          <button data-dart-filter="treasury">자사주</button>
          <button data-dart-filter="insider_increase">내부자 증가</button>
          <button data-dart-filter="insider_decrease">내부자 감소</button>
          <button data-dart-filter="major">5% 이상</button>
        </div>
        <button class="dart-activity-refresh" type="button" id="dart-activity-refresh"><i class="fa-solid fa-rotate-right"></i> 저장된 결과 새로고침</button>
      </div>
      <div class="dart-board" id="dart-board" data-mobile-market="KOSPI">
        <div class="dart-board-tabs" role="tablist" aria-label="시장 선택"></div>
        <p class="dart-activity-empty" id="dart-board-message"><i class="fa-solid fa-spinner fa-spin"></i> 저장된 수집 기록을 불러오는 중입니다.</p>
        <div class="dart-board-cols" hidden>
          ${BOARD_MARKETS.map(([key, label]) => `<section class="dart-board-col" data-board-market="${key}">
            <header class="dart-board-head is-${key === '기타' ? 'other' : key.toLowerCase()}"><h3>${label}</h3><b data-board-count="${key}">0건</b></header>
            <div class="dart-board-list" data-board-list="${key}"></div>
          </section>`).join('')}
        </div>
      </div>
      <p class="dart-activity-note" id="dart-activity-note"></p>
    </section>`;

  const board = container.querySelector('#dart-board');
  const tabs = container.querySelector('.dart-board-tabs');
  const cols = container.querySelector('.dart-board-cols');
  const message = container.querySelector('#dart-board-message');
  const summary = container.querySelector('#dart-activity-summary');
  const stamp = container.querySelector('#dart-activity-stamp');
  const note = container.querySelector('#dart-activity-note');
  const refresh = container.querySelector('#dart-activity-refresh');
  let items = [];
  let selectedFilter = 'all';

  const showMessage = (html) => {
    message.innerHTML = html;
    message.hidden = false;
    cols.hidden = true;
  };

  const render = () => {
    const visible = items.filter((item) => (GROUPS[selectedFilter] || GROUPS.all)(item.kind || ''));
    const byMarket = Object.fromEntries(BOARD_MARKETS.map(([key]) => [key, visible.filter((item) => marketOf(item) === key)]));
    for (const [key] of BOARD_MARKETS) {
      container.querySelector(`[data-board-list="${key}"]`).innerHTML = columnHtml(byMarket[key]);
      container.querySelector(`[data-board-count="${key}"]`).textContent = `${byMarket[key].length.toLocaleString()}건`;
    }
    // 코넥스·기타는 공시가 있을 때만 보인다(PC는 아래 전체 폭, 모바일은 세 번째 탭).
    const hasOther = byMarket['기타'].length > 0;
    container.querySelector('[data-board-market="기타"]').hidden = !hasOther;
    if (!hasOther && board.dataset.mobileMarket === '기타') board.dataset.mobileMarket = 'KOSPI';
    tabs.innerHTML = BOARD_MARKETS.filter(([key]) => key !== '기타' || hasOther).map(([key, label]) => `<button type="button" role="tab" data-board-tab="${key}" aria-selected="${board.dataset.mobileMarket === key}" class="${board.dataset.mobileMarket === key ? 'active' : ''}">${label} <b>${byMarket[key].length.toLocaleString()}</b></button>`).join('');
    message.hidden = true;
    cols.hidden = false;
  };

  const renderSummary = (counts) => {
    const cards = [
      ['treasury', '자사주 관련', 'fa-building-columns'],
      ['insider_increase', '내부자 증가', 'fa-arrow-trend-up'],
      ['insider_decrease', '내부자 감소', 'fa-arrow-trend-down'],
      ['major', '5% 이상 변동', 'fa-users'],
    ];
    summary.innerHTML = cards.map(([key, label, icon]) => `<article>
      <i class="fa-solid ${icon}" aria-hidden="true"></i><span>${label}</span><strong>${Number(counts?.[key] || 0).toLocaleString()}건</strong>
    </article>`).join('');
  };

  async function load() {
    refresh.disabled = true;
    try {
      const data = await api.dartMarketActivity();
      items = data.items || [];
      renderSummary(data.counts || {});
      const period = data.window_start && data.window_end
        ? `${formatDisclosureDate(data.window_start)} ~ ${formatDisclosureDate(data.window_end)}` : '최근 7일';
      stamp.textContent = data.snapshot_date
        ? `${period} · 최종 수집 ${data.snapshot_date}`
        : `${period} · 수집 기록 없음`;
      note.textContent = data.note || 'OpenDART 공시정보 · 읽기 전용';
      if (data.status === 'pending') {
        showMessage('<i class="fa-regular fa-clock"></i> 최근 7일 수집된 공시가 없습니다.');
      } else {
        render();
      }
    } catch (error) {
      // Codex 수정: DART-001 (2026-10-06), 조회 실패 시 이미 표시한 수집본 유지.
      if (items.length) {
        note.textContent = `새로고침 실패 · 이전에 표시한 공시를 유지합니다: ${error.message}`;
      } else {
        showMessage(`<span class="is-error">수집 기록을 불러오지 못했습니다: ${esc(error.message)}</span>`);
        note.textContent = 'OpenDART 수집 기록 조회 실패';
      }
    } finally {
      refresh.disabled = false;
    }
  }

  container.querySelector('.dart-activity-filters').addEventListener('click', (event) => {
    const button = event.target.closest('[data-dart-filter]');
    if (!button) return;
    selectedFilter = button.dataset.dartFilter;
    container.querySelectorAll('[data-dart-filter]').forEach((item) => item.classList.toggle('active', item === button));
    render();
  });
  tabs.addEventListener('click', (event) => {
    const button = event.target.closest('[data-board-tab]');
    if (!button) return;
    board.dataset.mobileMarket = button.dataset.boardTab;
    render();
  });
  refresh.addEventListener('click', load);
  load();
}
