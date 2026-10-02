const TOKEN_KEY = 'investment_analysis_auth_token';
let user = null;

function token() { return localStorage.getItem(TOKEN_KEY); }
function headers() { return token() ? { Authorization: `Bearer ${token()}` } : {}; }

export async function authRequest(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || '요청을 처리하지 못했습니다.');
  return payload;
}

export async function authDownload(path, fallbackFilename = 'download') {
  const response = await fetch(path, { headers: headers() });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || '파일을 내려받지 못했습니다.');
  }
  const disposition = response.headers.get('Content-Disposition') || '';
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] || fallbackFilename;
  const objectUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = objectUrl; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

export function isAuthenticated() { return Boolean(token()); }
export function currentUserInfo() { return user; }

function updateBadge() {
  const badge = document.getElementById('auth-badge');
  if (!badge) return;
  badge.innerHTML = user
    ? `<i class="fa-solid fa-user-check"></i><span>${user.username}</span>`
    : '<i class="fa-solid fa-user"></i><span>로그인</span>';
  badge.setAttribute('aria-label', user ? '내 계정과 사용 내역' : '로그인');
}

function modal() { return document.getElementById('auth-modal'); }
function closeModal() { modal()?.setAttribute('hidden', ''); document.body.classList.remove('modal-open'); }

async function renderActivity() {
  const target = document.getElementById('auth-activity');
  if (!target || !user) return;
  target.textContent = '사용 내역을 불러오는 중…';
  try {
    const data = await authRequest('/api/auth/activity');
    if (!data.items?.length) { target.textContent = '아직 저장된 사용 내역이 없습니다.'; return; }
    target.replaceChildren(...data.items.map((item) => {
      const row = document.createElement('li');
      const date = new Date(item.created_at).toLocaleString('ko-KR');
      row.textContent = `${item.view || item.action} · ${date}`;
      return row;
    }));
  } catch (error) { target.textContent = error.message; }
}

function showProfile() {
  const root = modal();
  root.querySelector('[data-auth-panel="form"]').hidden = true;
  root.querySelector('[data-auth-panel="profile"]').hidden = false;
  root.querySelector('#auth-profile-name').textContent = user.username;
  root.querySelector('#auth-profile-email').textContent = user.email || '네이버 계정';
  root.querySelector('#auth-profile-provider').textContent = user.auth_provider === 'naver' ? '네이버 연동 계정' : '네이버 미연동 계정';
  root.hidden = false;
  document.body.classList.add('modal-open');
  renderActivity();
}

function showForm() {
  const root = modal();
  root.querySelector('[data-auth-panel="profile"]').hidden = true;
  root.querySelector('[data-auth-panel="form"]').hidden = false;
  root.querySelector('#auth-error').textContent = '';
  root.hidden = false;
  document.body.classList.add('modal-open');
  root.querySelector('#naver-login').focus();
}

export function openAuthModal() { user ? showProfile() : showForm(); }

export async function recordUsage(view) {
  if (!user) return;
  try { await authRequest('/api/auth/activity', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'view', view }) }); } catch { /* 로그 저장 실패는 화면 이용을 막지 않는다. */ }
}

export async function initAuth() {
  document.body.insertAdjacentHTML('beforeend', `
    <div id="auth-modal" class="auth-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="auth-modal-title" hidden>
      <section class="auth-modal"><button type="button" class="auth-modal-close" aria-label="닫기"><i class="fa-solid fa-xmark"></i></button>
        <div data-auth-panel="form">
          <div class="auth-brand-mark" aria-hidden="true"><i class="fa-solid fa-chart-line"></i></div>
          <h2 id="auth-modal-title">로그인</h2>
          <p>네이버 계정으로 가입·로그인하면 전략 추천과 내 매매 기록·연구 화면을 이용할 수 있습니다.</p>
          <p class="auth-service-boundary"><i class="fa-solid fa-circle-info" aria-hidden="true"></i> EFA는 개인 포트폴리오·리서치 기록과 공개 시장 데이터 확인을 위한 서비스입니다. 네이버 로그인은 회원별 기록을 저장·조회하기 위한 본인 식별에만 사용합니다. 증권계좌 연동, 주문 전송·체결, 투자일임·자산운용은 제공하지 않습니다.</p>
          <a id="naver-login" class="auth-naver-login" href="/api/auth/naver/start"><img src="/assets/naver/NAVER_login_Dark_KR_green_wide_H56.png" alt="네이버 로그인" width="368" height="56"></a>
          <p class="auth-naver-note"><i class="fa-solid fa-shield-halved" aria-hidden="true"></i> 네이버 비밀번호는 EFA에 전달되지 않습니다. 받는 정보(이름·이메일)와 이용 목적은 <a href="/privacy.html" target="_blank" rel="noopener">개인정보처리방침</a>에 있습니다.</p>
          <p id="auth-error" class="auth-error" aria-live="polite"></p>
        </div>
        <div data-auth-panel="profile" hidden><h2>내 계정</h2><p id="auth-profile-name"></p><small id="auth-profile-email"></small><span id="auth-profile-provider" class="auth-provider"></span><h3>최근 사용 내역</h3><ul id="auth-activity" class="auth-activity"></ul><button type="button" id="auth-logout" class="auth-logout">로그아웃</button>
          <details class="auth-legacy auth-withdraw"><summary>회원 탈퇴</summary>
            <form id="auth-withdraw-form"><p class="auth-naver-note">탈퇴하면 계정 정보(네이버 식별자·이름·이메일), 로그인 세션, 이용 내역, 관심종목, 리서치 노트가 즉시 삭제됩니다. 자세한 내용은 <a href="/privacy.html" target="_blank" rel="noopener">개인정보처리방침</a>을 확인하세요.</p>
              <label>확인을 위해 '탈퇴'를 입력하세요<input id="auth-withdraw-confirm" autocomplete="off" required></label>
              <p id="auth-withdraw-error" class="auth-error" aria-live="polite"></p>
              <button type="submit" class="auth-submit auth-withdraw-submit">회원 탈퇴</button></form>
          </details></div>
      </section>
    </div>`);
  document.getElementById('auth-badge')?.addEventListener('click', openAuthModal);
  modal().querySelector('.auth-modal-close').addEventListener('click', closeModal);
  modal().addEventListener('click', (event) => { if (event.target === modal()) closeModal(); });
  // 로그아웃하면 화면을 새로 그려 인사말의 이름·내 자산·추천 판단 같은 개인 정보가 남지 않게 한다.
  modal().querySelector('#auth-logout').addEventListener('click', async () => { try { await authRequest('/api/auth/logout', { method: 'POST' }); } catch {} localStorage.removeItem(TOKEN_KEY); user = null; updateBadge(); closeModal(); window.location.reload(); });
  modal().querySelector('#auth-withdraw-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const error = modal().querySelector('#auth-withdraw-error');
    const confirmText = modal().querySelector('#auth-withdraw-confirm').value.trim();
    if (confirmText !== '탈퇴') { error.textContent = "'탈퇴'를 정확히 입력하세요."; return; }
    try {
      await authRequest('/api/auth/me', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: confirmText }) });
      localStorage.removeItem(TOKEN_KEY); user = null; updateBadge(); closeModal();
      window.setTimeout(() => window.location.reload(), 0);
      window.alert('탈퇴가 완료됐습니다. 네이버 연동 해제는 네이버 > 내정보 > 이력관리 > 연결된 서비스 관리에서 할 수 있습니다.');
    } catch (err) { error.textContent = err.message; }
  });
  if (token()) { try { user = (await authRequest('/api/auth/me')).user; } catch { localStorage.removeItem(TOKEN_KEY); } }
  updateBadge();
}
