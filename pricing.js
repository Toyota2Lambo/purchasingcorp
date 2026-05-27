(function () {
  const tabs = document.querySelectorAll('#tabs .tab');
  const rowsEl = document.getElementById('rows');
  const headEl = document.getElementById('tableHead');
  const emptyEl = document.getElementById('emptyState');
  const searchEl = document.getElementById('search');

  let activeCat = 'iphone';
  let query = '';

  // Try to load live data from the sheet via /api/pricing.
  // Falls back to the hardcoded window.PRICING snapshot on any failure.
  fetch('/api/pricing', { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error('bad status'))))
    .then((j) => {
      if (j && j.ok && j.data) {
        window.PRICING = j.data;
        render();
      }
    })
    .catch(() => { /* silent fall-through to snapshot */ });

  function setTabStyles() {
    tabs.forEach((t) => {
      const isActive = t.dataset.cat === activeCat;
      t.className = isActive
        ? 'tab rounded-full bg-white text-black px-4 py-2 font-medium'
        : 'tab rounded-full border border-white/10 bg-white/[0.04] px-4 py-2 text-ink-200 hover:bg-white/[0.08]';
    });
  }

  function render() {
    const data = window.PRICING[activeCat];
    if (!data) return;

    headEl.innerHTML = `
      <div class="col-span-6 md:col-span-7">${data.headers[0]}</div>
      <div class="col-span-3 md:col-span-3 text-right">${data.headers[1]}</div>
      <div class="col-span-3 md:col-span-2 text-right">${data.headers[2]}</div>
    `;

    const q = query.trim().toLowerCase();
    const filtered = q
      ? data.rows.filter((r) => r[0].toLowerCase().includes(q))
      : data.rows;

    if (!filtered.length) {
      rowsEl.innerHTML = '';
      emptyEl.classList.remove('hidden');
      return;
    }
    emptyEl.classList.add('hidden');

    rowsEl.innerHTML = filtered
      .map(
        (r) => `
        <li class="grid grid-cols-12 items-center px-5 py-3.5 hover:bg-white/[0.02] transition">
          <div class="col-span-6 md:col-span-7 text-ink-100">${escapeHtml(r[0])}</div>
          <div class="col-span-3 md:col-span-3 text-right font-semibold tabular-nums">${escapeHtml(r[1])}</div>
          <div class="col-span-3 md:col-span-2 text-right text-ink-300 tabular-nums">${escapeHtml(r[2])}</div>
        </li>`
      )
      .join('');
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  tabs.forEach((t) =>
    t.addEventListener('click', () => {
      activeCat = t.dataset.cat;
      setTabStyles();
      render();
    })
  );

  searchEl.addEventListener('input', (e) => {
    query = e.target.value || '';
    // If searching, try to auto-pick the category whose first row matches
    if (query.trim()) {
      for (const [cat, data] of Object.entries(window.PRICING)) {
        if (data.rows.some((r) => r[0].toLowerCase().includes(query.toLowerCase()))) {
          if (cat !== activeCat) {
            activeCat = cat;
            setTabStyles();
          }
          break;
        }
      }
    }
    render();
  });

  const yr = document.getElementById('year');
  if (yr) yr.textContent = new Date().getFullYear();

  setTabStyles();
  render();
})();
