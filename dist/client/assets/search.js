
document.querySelectorAll('[data-search-input]').forEach((input) => {
  const list = input.closest('main').querySelector('[data-search-list]');
  if (!list) return;
  const status = input.closest('main').querySelector('[data-search-status]');
  const empty = input.closest('main').querySelector('[data-search-empty]');
  const items = Array.from(list.querySelectorAll('[data-search-item]'));
  const groups = Array.from(list.querySelectorAll('.subarea-group'));
  const kind = input.dataset.searchKind || 'result';

  const update = () => {
    const query = input.value.trim().toLowerCase();
    let visible = 0;
    items.forEach((item) => {
      const text = item.getAttribute('data-search-text') || item.textContent.toLowerCase();
      item.hidden = query.length > 0 && !text.includes(query);
      if (!item.hidden) visible += 1;
    });

    groups.forEach((group) => {
      const groupItems = Array.from(group.querySelectorAll('[data-search-item]'));
      group.hidden = query.length > 0 && groupItems.every((item) => item.hidden);
    });

    if (status) {
      status.textContent = query
        ? `${visible} matching ${kind}${visible === 1 ? '' : 's'}`
        : `${items.length} ${kind}${items.length === 1 ? '' : 's'} available`;
    }
    if (empty) empty.hidden = visible !== 0;
  };

  input.addEventListener('input', update);
  update();
});
