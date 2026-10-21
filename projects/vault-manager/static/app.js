const token = document.querySelector('meta[name="vault-token"]').content;
const state = { items: [], tags: [], counts: {}, rules: {}, placeholders: {}, selected: null, replace: null };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers['content-type'] = 'application/json';
  if (options.method && options.method !== 'GET') headers['x-vault-token'] = token;
  const response = await fetch(path, { ...options, headers });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败：${response.status}`);
  return data;
}

function toast(message) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove('show'), 3000);
}

function esc(value = '') {
  return String(value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
}

function labelKind(kind) {
  return ({ raw: '文章', blog: '博客', software: '软件', wiki: 'Wiki', 'app-note': '应用笔记', daily: '日记', note: '笔记', output: '报告' })[kind] || kind;
}

function switchView(name) {
  $$('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === name));
  $$('.view').forEach(view => view.classList.toggle('active', view.id === `${name}View`));
  const titles = {
    dashboard: ['VAULT / OVERVIEW', '今天新增了什么？'],
    content: ['VAULT / METADATA', '统一管理笔记属性'],
    tags: ['VAULT / TAGS', '保持标签少而准确']
  };
  $('#viewEyebrow').textContent = titles[name][0];
  $('#viewTitle').textContent = titles[name][1];
}

function card(item) {
  const stateClass = item.publish === false ? 'private' : item.publish === true ? 'public' : 'neutral';
  const stateText = item.publish === true ? '公开' : item.publish === false ? '私有' : labelKind(item.kind);
  return `<article class="content-card card"><span class="path">${esc(item.relative)}</span><h3>${esc(item.title)}</h3><p>${esc(item.summary || '尚未填写摘要')}</p><div class="card-foot"><span class="status-pill ${stateClass}">${stateText}</span><time>${esc(item.created || item.updated || '')}</time></div></article>`;
}

function renderDashboard() {
  const month = new Date().toLocaleDateString('sv-SE').slice(0, 7);
  $('#monthLabel').textContent = month;
  $('#totalCount').textContent = state.items.length;
  $('#publicCount').textContent = state.items.filter(item => item.publish === true).length;
  $('#monthCount').textContent = state.items.filter(item => item.kind === 'raw' && item.created?.startsWith(month)).length;
  $('#monthCountLabel').textContent = `raw / ${month}`;
  const created = [...state.items].sort((a, b) => (b.created || '').localeCompare(a.created || '') || (b.updated || '').localeCompare(a.updated || '')).slice(0, 6);
  const updated = state.items.filter(item => item.updated).sort((a, b) => b.updated.localeCompare(a.updated)).slice(0, 8);
  $('#recentCreated').innerHTML = created.map(card).join('') || '<p class="empty-line">暂时没有内容。</p>';
  $('#recentUpdated').innerHTML = updated.map(item => `<div class="compact-row"><time>${esc(item.updated)}</time><strong>${esc(item.title)}</strong><span>${labelKind(item.kind)}</span></div>`).join('') || '<p class="empty-line">暂时没有更新。</p>';
}

function renderTagPickers() {
  $('#createTags').innerHTML = state.tags.map(tag => `<label><input type="checkbox" name="tags" value="${tag}"><span>${tag}</span></label>`).join('');
  const select = $('#replaceTagForm select[name="to"]');
  select.innerHTML = '<option value="">删除标签</option>' + state.tags.map(tag => `<option value="${tag}">${tag}</option>`).join('');
  $('#tagStats').innerHTML = state.tags.map(tag => `<article class="tag-cell card"><code>${tag}</code><strong>${state.counts[tag] || 0}</strong><span>篇内容</span></article>`).join('');
}

function filteredItems() {
  const q = $('#searchInput').value.trim().toLowerCase();
  const kind = $('#kindFilter').value;
  const publish = $('#publishFilter').value;
  return state.items.filter(item => (!q || item.title.toLowerCase().includes(q) || item.relative.toLowerCase().includes(q)) && (!kind || item.kind === kind) && (!publish || String(item.publish) === publish));
}

function renderContentList() {
  $('#contentList').innerHTML = filteredItems().map(item => `<button class="content-item ${state.selected?.relative === item.relative ? 'active' : ''}" data-path="${esc(item.relative)}"><span class="item-kind">${labelKind(item.kind)}</span><strong>${esc(item.title)}</strong><small>${esc(item.relative)}</small><span class="item-state ${item.publish === true ? 'public' : 'private'}">${item.publish === true ? '公开' : item.publish === false ? '私有' : '无公开字段'}</span></button>`).join('') || '<div class="empty-copy"><strong>没有匹配内容</strong><span>调整筛选条件后重试。</span></div>';
}

function editableFields(item) {
  const base = [['title', '标题', 'text'], ['summary', '摘要', 'textarea'], ['tags', '标签', 'tags']];
  if (item.kind === 'raw') base.push(['source', '来源', 'url'], ['author', '作者', 'list'], ['published', '原始发布日期', 'date'], ['publish', '公开', 'checkbox']);
  if (item.kind === 'blog') base.push(['category', '分类', 'text'], ['status', '状态', 'enum'], ['publish', '公开', 'checkbox']);
  if (item.kind === 'software') base.push(['category', '分类', 'text'], ['status', '状态', 'enum'], ['platforms', '平台', 'list'], ['pricing', '价格类型', 'enum'], ['open_source', '开源状态', 'open-source'], ['official_url', '官网', 'url'], ['download_url', '下载地址', 'url'], ['sources', '事实来源', 'list'], ['publish', '公开', 'checkbox']);
  if (item.kind === 'wiki') base.push(['sources', '来源', 'list'], ['status', '状态', 'enum'], ['publish', '公开', 'checkbox']);
  if (item.kind === 'app-note') base.push(['related_software', '关联软件 ID', 'text'], ['publish', '公开', 'checkbox']);
  return base;
}

const placeholders = {
  title: '输入可检索的标题', summary: '将从正文首段生成，也可以手动填写', source: '留空时可一键填入可公开的安全默认值',
  author: '多个作者用逗号分隔', category: '未分类', official_url: '待补充官方网站', download_url: '待补充官方下载地址',
  platforms: 'Windows, macOS, Linux', sources: '多个来源用逗号分隔', related_software: '例如：obsidian'
};

function enumOptions(item, name, value) {
  const values = state.rules[item.kind]?.enums?.[name] || [];
  return values.map(option => `<option value="${esc(option)}" ${option === value ? 'selected' : ''}>${esc(option)}</option>`).join('');
}

function fieldHtml(item, [name, label, type]) {
  const value = item.metadata?.[name];
  const hint = placeholders[name] || `填写 ${label}`;
  if (type === 'textarea') return `<label><span>${label}</span><textarea class="form-control" name="${name}" rows="3" placeholder="${esc(hint)}">${esc(value || '')}</textarea></label>`;
  if (type === 'checkbox') return `<label class="form-check metadata-check"><input class="form-check-input" name="${name}" type="checkbox" ${value === true ? 'checked' : ''}><span class="form-check-label">${label}</span></label>`;
  if (type === 'tags') return `<label><span>${label}</span><div class="tag-picker">${state.tags.map(tag => `<label><input type="checkbox" name="tags" value="${tag}" ${(item.tags || []).includes(tag) ? 'checked' : ''}><span>${tag}</span></label>`).join('')}</div></label>`;
  if (type === 'enum') return `<label><span>${label}</span><select class="form-select" name="${name}">${enumOptions(item, name, value)}</select></label>`;
  if (type === 'open-source') return `<label><span>${label}</span><select class="form-select" name="${name}">${['unknown', 'true', 'false'].map(option => `<option value="${option}" ${String(value) === option ? 'selected' : ''}>${option === 'unknown' ? '待确认' : option === 'true' ? '开源' : '闭源'}</option>`).join('')}</select></label>`;
  const display = Array.isArray(value) ? value.join(', ') : value || '';
  return `<label><span>${label}</span><input class="form-control" name="${name}" type="${type === 'list' ? 'text' : type}" value="${esc(display)}" placeholder="${esc(hint)}"></label>`;
}

function isEmpty(value) {
  if (value === undefined || value === null || value === '') return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') return Object.keys(value).length === 0;
  return false;
}

function isPlaceholder(field, value) {
  return state.placeholders[field] !== undefined && String(value) === String(state.placeholders[field]);
}

function completionInfo(item, form = null) {
  const rule = state.rules[item.kind] || {};
  const current = { ...(item.metadata || {}) };
  if (form) {
    for (const [name, , type] of editableFields(item)) {
      if (type === 'tags') current[name] = new FormData(form).getAll('tags');
      else if (type === 'checkbox') current[name] = form.elements[name]?.checked === true;
      else if (type === 'list') current[name] = (form.elements[name]?.value || '').split(',').map(value => value.trim()).filter(Boolean);
      else current[name] = form.elements[name]?.value || '';
    }
  }
  const missing = (rule.required || []).filter(field => !Object.prototype.hasOwnProperty.call(current, field));
  const publishNeeds = (rule.nonEmptyWhenPublished || []).filter(field => isEmpty(current[field]));
  const safeDefaults = Object.keys(state.placeholders).filter(field => isPlaceholder(field, current[field]));
  return { missing, publishNeeds, safeDefaults };
}

function completionHtml(item, form = null) {
  const { missing, publishNeeds, safeDefaults } = completionInfo(item, form);
  if (!missing.length && !publishNeeds.length) {
    const detail = safeDefaults.length ? `已使用可公开安全默认值：${safeDefaults.join('、')}；以后可按需替换。` : '当前字段满足 Schema 的公开要求。';
    return `<div class="completion-box complete"><div><strong>可以直接公开</strong><span>${esc(detail)}</span></div></div>`;
  }
  const details = [missing.length ? `缺少字段：${missing.join('、')}` : '', publishNeeds.length ? `公开前需补充：${publishNeeds.join('、')}` : ''].filter(Boolean).join('；');
  const button = missing.length || publishNeeds.length ? '<button type="button" class="btn btn-sm btn-warning" id="fillDefaults">一键补齐可公开属性</button>' : '';
  return `<div class="completion-box warning"><div><strong>属性尚未填满</strong><span>${esc(details)}</span></div>${button}</div>`;
}

function renderMetadata(item) {
  state.selected = item;
  renderContentList();
  const fields = editableFields(item).map(field => fieldHtml(item, field)).join('');
  const panel = $('#metadataForm');
  panel.classList.remove('empty');
  panel.innerHTML = `<div class="metadata-head"><div><span class="status-pill ${item.publish === true ? 'public' : 'private'}">${item.publish === true ? '公开' : '私有'}</span><h3>${esc(item.title)}</h3></div><code>${esc(item.relative)}</code></div><div id="completionArea">${completionHtml(item)}</div><div class="metadata-fields">${fields}</div><div class="inline-actions"><button type="submit" class="btn btn-primary">保存属性</button><button type="button" class="btn btn-outline-secondary" id="openSelected">在 Obsidian 中打开</button></div>`;
}

async function loadAll() {
  $('#statusText').textContent = '正在读取 Vault';
  const selectedPath = state.selected?.relative;
  const [itemsData, tagsData, schemaData] = await Promise.all([api('/api/items'), api('/api/tags'), api('/api/schema')]);
  state.items = itemsData.items;
  state.tags = tagsData.semantic;
  state.counts = tagsData.counts;
  state.rules = schemaData.types;
  state.placeholders = schemaData.placeholders || {};
  if (selectedPath) state.selected = state.items.find(item => item.relative === selectedPath) || null;
  renderDashboard();
  renderTagPickers();
  renderContentList();
  $('#statusText').textContent = `已读取 ${state.items.length} 篇内容 · Schema v${schemaData.version}`;
}

function setCreateKind(kind) {
  $$('.type-switch button').forEach(node => node.classList.toggle('active', node.dataset.kind === kind));
  $('#createForm input[name="kind"]').value = kind;
  $$('.article-field').forEach(node => node.classList.toggle('hidden', kind !== 'article'));
  $$('.blog-field').forEach(node => node.classList.toggle('hidden', kind !== 'blog'));
  $$('.software-field').forEach(node => node.classList.toggle('hidden', kind !== 'software'));
  $('#createForm input[name="publish"]').checked = kind === 'article';
  $('#createHint').textContent = kind === 'article' ? '文章保存到本月 raw 目录。缺来源时会自动使用可公开安全默认值。' : kind === 'blog' ? '博客默认私有。' : '软件条目默认私有。';
}

function closeCreateDialog() {
  $('#createDialog').close();
  $('#createResult').className = 'result-panel hidden';
}

$$('.nav-item').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));
$$('[data-jump]').forEach(button => button.addEventListener('click', () => switchView(button.dataset.jump)));
document.addEventListener('keydown', event => {
  if (/^[1-3]$/.test(event.key) && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) $$('.nav-item')[Number(event.key) - 1].click();
});

$('#openCreate').addEventListener('click', () => $('#createDialog').showModal());
$('#closeCreate').addEventListener('click', closeCreateDialog);
$('#cancelCreate').addEventListener('click', closeCreateDialog);
$('#createDialog').addEventListener('click', event => { if (event.target === event.currentTarget) closeCreateDialog(); });
$$('.type-switch button').forEach(button => button.addEventListener('click', () => setCreateKind(button.dataset.kind)));

$('#createForm').addEventListener('submit', async event => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = {
    kind: form.get('kind'), title: form.get('title'), summary: form.get('summary'), tags: form.getAll('tags'),
    publish: form.get('publish') === 'on', source: form.get('source') || null,
    author: (form.get('author') || '').split(',').map(value => value.trim()).filter(Boolean),
    published: form.get('published') || null, category: form.get('category') || null, software_id: form.get('software_id') || null
  };
  try {
    const data = await api('/api/content', { method: 'POST', body: JSON.stringify(payload) });
    const result = $('#createResult');
    result.className = 'result-panel';
    result.innerHTML = `已创建 <strong>${esc(data.item.relative)}</strong>。<button type="button" class="btn btn-link btn-sm" id="openCreated">在 Obsidian 中打开</button>`;
    $('#openCreated').onclick = () => api('/api/open', { method: 'POST', body: JSON.stringify({ path: data.item.relative }) });
    event.currentTarget.reset();
    setCreateKind('article');
    await loadAll();
    toast('文件已创建');
  } catch (error) {
    const result = $('#createResult');
    result.className = 'result-panel error';
    result.textContent = error.message;
  }
});

$('#contentList').addEventListener('click', event => {
  const button = event.target.closest('[data-path]');
  if (!button) return;
  const item = state.items.find(candidate => candidate.relative === button.dataset.path);
  if (item) renderMetadata(item);
});
['searchInput', 'kindFilter', 'publishFilter'].forEach(id => $(`#${id}`).addEventListener('input', renderContentList));

$('#metadataForm').addEventListener('input', event => {
  if (!state.selected || !event.target.name) return;
  $('#completionArea').innerHTML = completionHtml(state.selected, event.currentTarget);
});
$('#metadataForm').addEventListener('change', event => {
  if (!state.selected || !event.target.name) return;
  $('#completionArea').innerHTML = completionHtml(state.selected, event.currentTarget);
});

$('#metadataForm').addEventListener('submit', async event => {
  event.preventDefault();
  if (!state.selected) return;
  const form = new FormData(event.currentTarget);
  const fields = {};
  for (const [name, , type] of editableFields(state.selected)) {
    let next;
    if (type === 'tags') next = form.getAll('tags');
    else if (type === 'checkbox') next = form.get(name) === 'on';
    else if (['list'].includes(type)) next = (form.get(name) || '').split(',').map(value => value.trim()).filter(Boolean);
    else if (name === 'author') next = (form.get(name) || '').split(',').map(value => value.trim()).filter(Boolean);
    else if (type === 'open-source') next = form.get(name) === 'unknown' ? 'unknown' : form.get(name) === 'true';
    else next = form.get(name) || '';
    const current = state.selected.metadata?.[name];
    const unchanged = Array.isArray(next)
      ? Array.isArray(current) && next.length === current.length && next.every((value, index) => value === current[index])
      : type === 'checkbox' ? next === current : next === current || String(next) === String(current ?? '');
    if (!unchanged) fields[name] = next;
  }
  if (!Object.keys(fields).length) { toast('没有需要保存的属性'); return; }
  try {
    const data = await api('/api/content/metadata', { method: 'PATCH', body: JSON.stringify({ path: state.selected.relative, expected_mtime: state.selected.mtime, fields }) });
    await loadAll();
    renderMetadata(data.item);
    toast('属性已保存');
  } catch (error) {
    const failedPath = state.selected.relative;
    await loadAll();
    const latest = state.items.find(item => item.relative === failedPath);
    if (latest) renderMetadata(latest);
    toast(`保存失败：${error.message}`);
  }
});

$('#metadataForm').addEventListener('click', async event => {
  if (!state.selected) return;
  if (event.target.id === 'openSelected') await api('/api/open', { method: 'POST', body: JSON.stringify({ path: state.selected.relative }) });
  if (event.target.id === 'fillDefaults') {
    try {
      const data = await api('/api/content/fill-defaults', { method: 'POST', body: JSON.stringify({ path: state.selected.relative }) });
      await loadAll();
      renderMetadata(data.item);
      const placeholderApplied = data.applied.filter(field => isPlaceholder(field, data.item.metadata?.[field]));
      const defaultsApplied = data.applied.filter(field => !placeholderApplied.includes(field));
      const parts = [];
      if (defaultsApplied.length) parts.push(`已补齐：${defaultsApplied.join('、')}`);
      if (placeholderApplied.length) parts.push(`已填入可公开安全默认值：${placeholderApplied.join('、')}`);
      if (data.unresolved.length) parts.push(`仍需补充：${data.unresolved.join('、')}`);
      if (!data.unresolved.length) parts.push('现在可以直接公开');
      toast(parts.join('；') || '属性已经是最新状态');
    } catch (error) { toast(`补齐失败：${error.message}`); }
  }
});

$('#replaceTagForm').addEventListener('submit', async event => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  state.replace = { from: form.get('from'), to: form.get('to') || null };
  try {
    const data = await api('/api/tags/replace', { method: 'POST', body: JSON.stringify({ ...state.replace, preview: true }) });
    const panel = $('#tagReplaceResult');
    panel.className = 'result-panel';
    panel.innerHTML = `将影响 <strong>${data.affected.length}</strong> 个文件。${data.affected.slice(0, 12).map(esc).join('<br>')}`;
    $('#applyTagReplace').disabled = data.affected.length === 0;
  } catch (error) { toast(error.message); }
});

$('#applyTagReplace').addEventListener('click', async () => {
  if (!state.replace) return;
  const data = await api('/api/tags/replace', { method: 'POST', body: JSON.stringify({ ...state.replace, preview: false }) });
  toast(`已更新 ${data.affected.length} 个文件`);
  state.replace = null;
  $('#applyTagReplace').disabled = true;
  await loadAll();
});

$('#validateButton').addEventListener('click', async () => {
  $('#statusText').textContent = '正在校验全库';
  try {
    const report = await api('/api/validate', { method: 'POST' });
    $('#issueCount').textContent = report.errors.length + report.warnings.length;
    $('#statusText').textContent = `校验完成：${report.errors.length} 错误，${report.warnings.length} 提示`;
    toast(report.errors.length ? '发现需要处理的校验错误' : '全库 Schema 校验通过');
  } catch (error) { toast(error.message); }
});

loadAll().catch(error => { $('#statusText').textContent = '读取失败'; toast(error.message); });
