const form = document.querySelector('#filters');
const search = document.querySelector('#search');
const year = document.querySelector('#year');
const role = document.querySelector('#role');
const count = document.querySelector('#result-count');
const empty = document.querySelector('#empty');

function normalize(value) {
  return value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase()
    .replace(/[’‘']/g, '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
}

const articles = [...document.querySelectorAll('#works tr')].map(row => {
  const link = row.querySelector('.article-title');
  const text = [link.textContent, link.href, row.dataset.person,
    row.querySelector('.person').textContent, row.querySelector('.article-note')?.textContent || ''];
  return { row, terms: normalize(text.join(' ')) };
});

function filterArticles() {
  const query = normalize(search.value).split(/\s+/).filter(Boolean);
  let matches = 0;
  for (const { row, terms } of articles) {
    const visible = (!year.value || row.dataset.year === year.value)
      && (!role.value || row.dataset.role === role.value)
      && query.every(word => terms.includes(word));
    row.hidden = !visible;
    if (visible) matches++;
  }
  const filtering = query.length || year.value || role.value;
  count.textContent = filtering ? `${matches} of ${articles.length} articles` : `${matches} articles`;
  empty.hidden = matches > 0;
}

search.addEventListener('input', filterArticles);
year.addEventListener('change', filterArticles);
role.addEventListener('change', filterArticles);
form.addEventListener('submit', event => event.preventDefault());
document.querySelector('#reset').addEventListener('click', () => {
  form.reset();
  filterArticles();
  search.focus();
});
window.addEventListener('pageshow', filterArticles);
form.hidden = false;
filterArticles();
