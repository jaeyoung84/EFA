/**
 * sidebarUI.js — 사이드바 열기/닫기, 섹션 토글 공통 로직.
 * index.html(SPA)과 pages/*.html 정적 페이지 양쪽에서 공유한다.
 */
const DESKTOP_BREAKPOINT = 1024;
let _sidebarOpen = window.innerWidth > DESKTOP_BREAKPOINT;
const MENU_SECTION_ORDER = ['market', 'strategy', 'portfolio', 'research', 'settings'];

// SPA와 정적 페이지가 같은 메뉴 순서를 유지하도록 실제 DOM 순서를 맞춘다.
function orderSidebarSections() {
  const nav = document.querySelector('.sidebar-nav');
  if (!nav) return;

  const sections = new Map(
    [...nav.querySelectorAll(':scope > .nav-section')].map((section) => {
      const id = MENU_SECTION_ORDER.find((item) => section.querySelector(`#nav-${item}`));
      return [id, section];
    }),
  );
  MENU_SECTION_ORDER.forEach((id) => {
    const section = sections.get(id);
    if (section) nav.append(section);
  });

}
window._orderSidebarSections = orderSidebarSections;

function ensureSidebarChatbot() {
  if (document.getElementById('floating-chatbot')) return;

  document.body.insertAdjacentHTML('beforeend', `
    <section class="floating-chatbot" id="floating-chatbot" aria-label="AI 투자 도우미">
      <div class="floating-chatbot-panel" id="floating-chatbot-panel" hidden>
        <header class="floating-chatbot-head">
          <span><i class="fa-solid fa-robot"></i> AI 투자 도우미</span>
          <button type="button" id="floating-chatbot-close" aria-label="대화창 닫기"><i class="fa-solid fa-xmark"></i></button>
        </header>
        <div class="floating-chatbot-messages" id="floating-chatbot-messages" aria-live="polite">
          <p class="floating-chatbot-welcome">투자와 종목에 관한 궁금한 내용을 입력해 보세요.</p>
        </div>
        <form class="floating-chatbot-form" id="floating-chatbot-form">
          <input id="floating-chatbot-input" type="text" maxlength="300" placeholder="질문을 입력하세요" aria-label="챗봇 질문" />
          <button type="submit" aria-label="질문 보내기"><i class="fa-solid fa-paper-plane"></i></button>
        </form>
      </div>
      <button type="button" class="floating-chatbot-trigger" id="floating-chatbot-trigger" aria-label="AI 투자 도우미 열기" aria-expanded="false">
        <i class="fa-solid fa-comment-dots"></i><span>AI 투자 도우미</span>
      </button>
    </section>`);

  const panel = document.getElementById('floating-chatbot-panel');
  const trigger = document.getElementById('floating-chatbot-trigger');
  const close = document.getElementById('floating-chatbot-close');
  const form = document.getElementById('floating-chatbot-form');
  const input = document.getElementById('floating-chatbot-input');
  const submit = form?.querySelector('button[type="submit"]');
  const messages = document.getElementById('floating-chatbot-messages');
  const sessionKey = 'investment_analysis_lex_session_id';
  const makeSessionId = () => `web-${window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
  let sessionId = localStorage.getItem(sessionKey) || makeSessionId();
  localStorage.setItem(sessionKey, sessionId);
  const appendMessage = (text, className) => {
    const message = document.createElement('p');
    message.className = `floating-chatbot-message ${className}`;
    message.textContent = text;
    messages?.append(message);
    messages?.scrollTo({ top: messages.scrollHeight, behavior: 'smooth' });
  };
  const setOpen = (open) => {
    panel.hidden = !open;
    trigger.setAttribute('aria-expanded', String(open));
    trigger.setAttribute('aria-label', open ? 'AI 투자 도우미 닫기' : 'AI 투자 도우미 열기');
    if (open) input?.focus();
  };
  trigger?.addEventListener('click', () => setOpen(panel.hidden));
  close?.addEventListener('click', () => setOpen(false));

  form?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const question = input?.value.trim();
    if (!question || !messages) return;

    appendMessage(question, 'is-user');
    input.value = '';
    input.disabled = true;
    if (submit) submit.disabled = true;
    try {
      const response = await fetch('/api/lex/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: question, session_id: sessionId }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || '투자 도우미 응답을 받지 못했습니다.');
      sessionId = payload.session_id || sessionId;
      localStorage.setItem(sessionKey, sessionId);
      (payload.messages || []).forEach((message) => appendMessage(message, 'is-enterprise'));
    } catch (error) {
      appendMessage(error.message || '투자 도우미 연결에 실패했습니다.', 'is-enterprise');
    } finally {
      input.disabled = false;
      if (submit) submit.disabled = false;
      input.focus();
    }
  });
}
window._ensureSidebarChatbot = ensureSidebarChatbot;

// ── Enterprise 안내 모달 (회원가입/로그인 클릭 시) ──
function openEnterpriseModal() {
  const overlay = document.getElementById('enterprise-modal-overlay');
  if (!overlay) return;
  overlay.hidden = false;
  document.body.classList.add('modal-open');
}
function closeEnterpriseModal() {
  const overlay = document.getElementById('enterprise-modal-overlay');
  if (!overlay) return;
  overlay.hidden = true;
  document.body.classList.remove('modal-open');
}
window.closeEnterpriseModal = closeEnterpriseModal;

document.querySelectorAll('.js-enterprise-gate').forEach((el) => {
  el.addEventListener('click', (event) => {
    event.preventDefault();
    openEnterpriseModal();
  });
});
document.getElementById('enterprise-modal-overlay')?.addEventListener('click', (event) => {
  if (event.target.id === 'enterprise-modal-overlay') closeEnterpriseModal();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeEnterpriseModal();
});

// ── 상단 메인 메뉴바(mainnav) — 좌측 사이드바를 대체한다 ──
// 데스크톱: 항상 가로로 표시, 드롭다운은 CSS :hover/:focus-within으로 연다.
// 모바일(<=1024px): 상단바 햄버거로 여닫는 세로 드롭다운 패널, 그룹은 탭해서 아코디언처럼 펼친다.
function syncMainnavToggle() {
  const toggle = document.getElementById('mainnav-toggle');
  if (!toggle) return;
  toggle.setAttribute('aria-expanded', String(_sidebarOpen));
  toggle.setAttribute('aria-label', _sidebarOpen ? '메뉴 닫기' : '메뉴 열기');
}

function toggleMainnav() {
  _sidebarOpen ? closeMainnav() : openMainnav();
}
function openMainnav() {
  document.getElementById('mainnav')?.classList.add('open');
  document.getElementById('overlay')?.classList.add('show');
  _sidebarOpen = true;
  syncMainnavToggle();
}
function closeMainnav() {
  document.getElementById('mainnav')?.classList.remove('open');
  document.getElementById('overlay')?.classList.remove('show');
  document.querySelectorAll('.mainnav-group.is-open').forEach((group) => group.classList.remove('is-open'));
  _sidebarOpen = false;
  syncMainnavToggle();
}
window.toggleMainnav = toggleMainnav;
window.closeMainnav = closeMainnav;
window.openMainnav = openMainnav;

// 모바일에서는 그룹 트리거를 탭하면 아코디언처럼 펼쳐진다(데스크톱은 :hover로 처리하므로 무시).
document.querySelectorAll('.mainnav-trigger').forEach((trigger) => {
  trigger.addEventListener('click', (event) => {
    if (window.innerWidth > DESKTOP_BREAKPOINT) return;
    event.preventDefault();
    const group = trigger.closest('.mainnav-group');
    if (!group) return;
    const isOpen = group.classList.toggle('is-open');
    trigger.setAttribute('aria-expanded', String(isOpen));
  });
});
// 모바일에서 실제 메뉴 항목을 선택하면 패널을 닫는다.
document.querySelectorAll('.mainnav .nav-item[data-view]').forEach((link) => {
  link.addEventListener('click', () => {
    if (window.innerWidth <= DESKTOP_BREAKPOINT) closeMainnav();
  });
});

// 현재 화면이 속한 상단 메뉴 그룹을 강조 표시한다(드롭다운을 계속 펼쳐두지는 않는다 — 호버/탭으로 여닫는 임시 패널이므로).
window._openNavSection = function(id) {
  document.querySelector(`.mainnav-group[data-group="${id}"]`)?.classList.add('is-active-group');
};
function clearActiveNavSections() {
  document.querySelectorAll('.mainnav-group.is-active-group').forEach((group) => group.classList.remove('is-active-group'));
}
window._setActiveNavSections = function(ids) {
  clearActiveNavSections();
  (ids || []).forEach((id) => window._openNavSection(id));
};

window.addEventListener('resize', () => {
  if (window.innerWidth > DESKTOP_BREAKPOINT) {
    closeMainnav();
  }
});

ensureSidebarChatbot();
syncMainnavToggle();
