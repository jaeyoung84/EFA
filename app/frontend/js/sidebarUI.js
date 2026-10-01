/**
 * sidebarUI.js — 사이드바 열기/닫기, 섹션 토글 공통 로직.
 * index.html(SPA)의 상단 메뉴를 여닫는다.
 */
const DESKTOP_BREAKPOINT = 1024;
let _sidebarOpen = window.innerWidth > DESKTOP_BREAKPOINT;

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

syncMainnavToggle();
