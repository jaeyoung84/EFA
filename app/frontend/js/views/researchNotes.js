// 리서치 노트: 종목·태그를 붙인 개인 메모를 쓰고, 고치고, 찾는다. 본인 계정에만 저장된다.
import { authRequest, isAuthenticated, openAuthModal } from '../auth.js';
import { attachStockSearch } from '../utils/stockSearch.js';

const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));
const kst = (value) => (value ? new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '-');

export function notesListHtml(notes, activeId = null) {
  if (!notes.length) return '<div class="dash-empty"><p>노트가 없습니다. "새 노트"로 첫 메모를 남겨 보세요.</p></div>';
  return `<ul class="efa-note-list">${notes.map((note) => `<li><button type="button" class="${note.id === activeId ? 'is-active' : ''}" data-note-open="${esc(note.id)}">
    <strong>${esc(note.title)}</strong>
    <span>${esc(note.body.slice(0, 80))}${note.body.length > 80 ? '…' : ''}</span>
    <small>${note.tickers.map((t) => `<i>${esc(t.name)}</i>`).join('')}${note.tags.map((tag) => `<i class="is-tag">#${esc(tag)}</i>`).join('')}<time>${esc(kst(note.updated_at))}</time></small>
  </button></li>`).join('')}</ul>`;
}

export function researchNotesView(container) {
  container.innerHTML = `<div class="efa-page">
    <header class="efa-page-head"><div><h1><i class="fa-solid fa-book-open"></i> 리서치 노트</h1>
      <p>종목 분석·업황·아이디어를 메모로 남기고 종목·태그로 다시 찾습니다.</p></div></header>
    <section class="dash-card" data-notes-body></section></div>`;
  const body = container.querySelector('[data-notes-body]');
  if (!isAuthenticated()) {
    body.innerHTML = '<div class="dash-empty"><p>리서치 노트는 로그인한 회원 본인 계정에 저장됩니다.</p><button type="button" class="dash-btn is-primary" data-login>로그인</button></div>';
    body.querySelector('[data-login]').addEventListener('click', openAuthModal);
    return;
  }
  body.innerHTML = `<div class="efa-notes">
    <aside><div class="efa-form-row"><input type="search" data-note-q placeholder="제목·내용·종목 검색" aria-label="노트 검색">
      <button type="button" class="dash-btn is-primary" data-note-new>새 노트</button></div>
      <div data-note-list><p class="efa-muted">불러오는 중…</p></div></aside>
    <form class="efa-note-editor" data-note-form>
      <input name="title" maxlength="120" placeholder="제목" aria-label="제목" required>
      <div class="efa-form-row"><input type="text" data-note-ticker placeholder="종목 연결: 종목명 검색 후 선택" aria-label="연결할 종목">
        <input name="tags" placeholder="태그(쉼표로 구분, 예: 반도체, 실적)" aria-label="태그"></div>
      <div class="efa-chip-row" data-note-tickers></div>
      <textarea name="body" maxlength="20000" rows="14" placeholder="내용" aria-label="내용"></textarea>
      <div class="efa-form-row is-end"><span class="efa-muted" data-note-meta></span>
        <button type="button" class="dash-btn is-danger" data-note-delete hidden>삭제</button>
        <button type="submit" class="dash-btn is-primary">저장</button></div>
      <p class="efa-error" role="status" data-note-status></p>
    </form></div>`;
  const form = body.querySelector('[data-note-form]');
  const list = body.querySelector('[data-note-list]');
  const status = body.querySelector('[data-note-status]');
  let notes = [];
  let current = null;
  let tickers = [];
  let timer = null;

  const renderTickers = () => {
    body.querySelector('[data-note-tickers]').innerHTML = tickers.map((item, index) => `<span class="efa-chip">${esc(item.name)}<button type="button" data-remove-ticker="${index}" aria-label="${esc(item.name)} 연결 해제">×</button></span>`).join('');
    body.querySelectorAll('[data-remove-ticker]').forEach((button) => button.addEventListener('click', () => { tickers.splice(Number(button.dataset.removeTicker), 1); renderTickers(); }));
  };
  const edit = (note) => {
    current = note;
    form.elements.title.value = note?.title || '';
    form.elements.body.value = note?.body || '';
    form.elements.tags.value = (note?.tags || []).join(', ');
    tickers = (note?.tickers || []).map((item) => ({ ...item }));
    renderTickers();
    body.querySelector('[data-note-delete]').hidden = !note;
    body.querySelector('[data-note-meta]').textContent = note ? `마지막 수정 ${kst(note.updated_at)}` : '새 노트';
    status.textContent = '';
    list.querySelectorAll('[data-note-open]').forEach((button) => button.classList.toggle('is-active', button.dataset.noteOpen === note?.id));
  };
  async function load(q = '') {
    try {
      notes = (await authRequest(`/api/notes${q ? `?q=${encodeURIComponent(q)}` : ''}`)).items;
      list.innerHTML = notesListHtml(notes, current?.id);
      list.querySelectorAll('[data-note-open]').forEach((button) => button.addEventListener('click', () => edit(notes.find((note) => note.id === button.dataset.noteOpen))));
    } catch (error) { list.innerHTML = `<p class="efa-error">${esc(error.message)}</p>`; }
  }

  attachStockSearch(body.querySelector('[data-note-ticker]'), { onSelect: (item) => {
    if (!tickers.some((t) => t.ticker === item.ticker) && tickers.length < 10) tickers.push({ ticker: item.ticker, name: item.name });
    body.querySelector('[data-note-ticker]').value = '';
    renderTickers();
  } });
  body.querySelector('[data-note-q]').addEventListener('input', (event) => { clearTimeout(timer); timer = setTimeout(() => load(event.target.value.trim()), 250); });
  body.querySelector('[data-note-new]').addEventListener('click', () => edit(null));
  body.querySelector('[data-note-delete]').addEventListener('click', async () => {
    if (!current || !window.confirm(`"${current.title}" 노트를 삭제할까요?`)) return;
    try { await authRequest(`/api/notes/${current.id}`, { method: 'DELETE' }); edit(null); load(); } catch (error) { status.textContent = error.message; }
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = { title: form.elements.title.value, body: form.elements.body.value,
      tickers: tickers.map((item) => item.ticker), tags: form.elements.tags.value.split(',').map((tag) => tag.trim()).filter(Boolean) };
    try {
      const saved = await authRequest(current ? `/api/notes/${current.id}` : '/api/notes', {
        method: current ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      current = saved;
      await load(body.querySelector('[data-note-q]').value.trim());
      edit(saved);
      status.textContent = '저장했습니다.';
    } catch (error) { status.textContent = error.message; }
  });
  edit(null);
  load();
}
