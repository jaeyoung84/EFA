// 국내 종목명 자동완성 (EINT-018). 엔진이 매일 게시하는 KRX 상장 목록(/api/market/instruments)에서 찾는다.
// attachStockSearch(input, { onSelect }) — 입력 아래에 후보 목록을 띄우고, 고르면 onSelect({ticker, name, market, code}).
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));

export function attachStockSearch(input, { onSelect, limit = 8 } = {}) {
  const wrapper = document.createElement('div');
  wrapper.className = 'stock-search';
  input.parentNode.insertBefore(wrapper, input);
  wrapper.appendChild(input);
  const list = document.createElement('ul');
  list.className = 'stock-search-list';
  list.hidden = true;
  wrapper.appendChild(list);
  input.setAttribute('autocomplete', 'off');

  let items = [];
  let active = -1;
  let timer = null;
  let requestId = 0;

  const close = () => { list.hidden = true; active = -1; };
  const choose = (item) => {
    input.value = item.name;
    input.dataset.ticker = item.ticker;
    close();
    onSelect?.(item);
  };
  const render = () => {
    if (!items.length) {
      list.innerHTML = '<li class="stock-search-empty">일치하는 국내 상장 종목이 없습니다.</li>';
    } else {
      list.innerHTML = items.map((item, index) => `<li class="stock-search-item${index === active ? ' is-active' : ''}" data-index="${index}">
        <strong>${esc(item.name)}</strong><span>${esc(item.code)} · ${esc(item.market)}</span></li>`).join('');
    }
    list.hidden = false;
  };
  const search = async () => {
    const query = input.value.trim();
    delete input.dataset.ticker;
    if (!query) { close(); return; }
    const current = ++requestId;
    try {
      const response = await fetch(`/api/market/instruments?q=${encodeURIComponent(query)}&limit=${limit}`);
      const data = await response.json();
      if (current !== requestId) return;
      items = data.items || [];
      active = items.length ? 0 : -1;
      render();
    } catch { if (current === requestId) close(); }
  };

  input.addEventListener('input', () => { window.clearTimeout(timer); timer = window.setTimeout(search, 150); });
  input.addEventListener('keydown', (event) => {
    if (list.hidden || !items.length) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      active = (active + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
      render();
    } else if (event.key === 'Enter' && active >= 0) {
      event.preventDefault();
      event.stopImmediatePropagation();
      choose(items[active]);
    } else if (event.key === 'Escape') {
      close();
    }
  });
  list.addEventListener('mousedown', (event) => {
    const row = event.target.closest('[data-index]');
    if (row) { event.preventDefault(); choose(items[Number(row.dataset.index)]); }
  });
  input.addEventListener('blur', () => window.setTimeout(close, 120));
  return { close };
}
