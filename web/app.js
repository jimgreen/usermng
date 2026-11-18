const pageKind = document.body.dataset.page || 'dashboard'
const resourceKinds = ['users', 'groups', 'roles', 'permissions']
const meta = {
  dashboard: { title: '管理仪表盘', description: '概览用户、用户组、角色和权限数据' },
  users: { title: '用户管理', description: '维护用户资料、状态、用户组和角色关系' },
  groups: { title: '用户组管理', description: '定义用户组并维护成员关系' },
  roles: { title: '角色管理', description: '定义角色并绑定用户和权限' },
  permissions: { title: '权限管理', description: '定义资源、动作和权限编码' },
}
const names = { users: '用户', groups: '用户组', roles: '角色', permissions: '权限' }
const endpoint = (kind) => kind === 'users' ? '/api/v1/users/users/' : kind === 'groups' ? '/api/v1/users/groups/' : `/api/v1/rbac/${kind}/`
const state = { kind: pageKind, items: [], selected: new Set(), page: 1, pageSize: 20, total: 0, search: '', status: 'all', groupId: '', sort: 'name', refs: { users: [], groups: [], roles: [], permissions: [] }, editing: null, summary: { users: 0, active_users: 0, groups: 0, roles: 0, permissions: 0, memberships: 0 }, insights: null }
const $ = (id) => document.getElementById(id)
const has = (id) => Boolean($(id))
const api = async (url, options = {}) => {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options })
  if (!response.ok) {
    let detail = response.statusText
    try { detail = (await response.json()).detail || detail } catch {}
    throw new Error(detail)
  }
  return response.status === 204 ? null : response.json()
}
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const formatDate = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '-'
const labels = (items) => (items || []).map((item) => `<span class="badge">${esc(item.name || item)}</span>`).join('') || '<span class="muted">-</span>'
const selectedIds = () => [...state.selected].map(Number)
const toast = (text) => { if (!has('message')) return; $('message').textContent = text; $('message').style.display = 'block'; clearTimeout(window.toastTimer); window.toastTimer = setTimeout(() => { $('message').style.display = 'none' }, 2400) }

function setupHeader() {
  const current = meta[pageKind] || meta.dashboard
  if (has('pageTitle')) $('pageTitle').textContent = current.title
  if (has('pageDescription')) $('pageDescription').textContent = current.description
  document.title = `${current.title} · 用户管理`
  document.querySelectorAll('.site-nav a').forEach((link) => link.classList.toggle('active', link.dataset.page === pageKind))
  api('/api/health').then((result) => { if (has('health')) $('health').textContent = `SQLite · ${result.database}` }).catch(() => { if (has('health')) $('health').textContent = '后端未连接' })
}

async function loadSummary() {
  try { state.summary = await api('/api/summary'); renderNavCounts(); if (pageKind === 'dashboard') renderSummary() } catch (error) { toast(error.message) }
}

function renderNavCounts() {
  document.querySelectorAll('[data-nav-count]').forEach((node) => { node.textContent = `(${state.summary[node.dataset.navCount] ?? 0})` })
}

function renderSummary() {
  if (!has('summary')) return
  const cards = [
    ['users', '用户总数', state.summary.users], ['active_users', '启用用户', state.summary.active_users],
    ['groups', '用户组', state.summary.groups], ['roles', '角色', state.summary.roles],
    ['permissions', '权限', state.summary.permissions], ['memberships', '用户组关系', state.summary.memberships],
  ]
  $('summary').innerHTML = cards.map(([key, label, value]) => {
    const target = key === 'active_users' || key === 'memberships' ? 'users' : key
    const suffix = key === 'active_users' ? '?status=active' : ''
    return `<a class="metric" href="/ui/${target}/${suffix}"><span>${label}</span><strong>${value ?? 0}</strong></a>`
  }).join('')
}

async function loadInsights() {
  try { state.insights = await api('/api/insights'); renderInsights(); if (has('insightStatus')) $('insightStatus').textContent = `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}` } catch (error) { if (has('insightStatus')) $('insightStatus').textContent = '统计不可用'; toast(error.message) }
}

function renderInsights() {
  const data = state.insights
  if (!data || !has('coverage')) return
  const total = Math.max(state.summary.users, 1)
  const rows = [['有用户组', data.coverage.users_with_group], ['无用户组', data.coverage.users_without_group], ['有角色', data.coverage.users_with_role], ['无角色', data.coverage.users_without_role]]
  $('coverage').innerHTML = rows.map(([label, value]) => `<div class="coverage-row"><label>${label}</label><div class="coverage-track"><i style="width:${Math.min(100, Math.round(value / total * 100))}%"></i></div><strong>${value}</strong></div>`).join('')
  const recent = data.recent_users || []
  $('recentUsers').innerHTML = recent.length ? `<ul class="mini-list">${recent.map((item) => `<li><span>${esc(item.name)} <span class="muted">(${esc(item.username)})</span></span><span>${formatDate(item.created_at)} <span class="status ${item.is_active ? 'on' : 'off'}">${item.is_active ? '启用' : '禁用'}</span></span></li>`).join('')}</ul>` : '<div class="panel-empty">暂无用户</div>'
  const groups = (data.top_groups || []).map((item) => `<li><span><em class="list-kind">组</em>${esc(item.name)}</span><span>${item.users_count} 人</span></li>`)
  const roles = (data.top_roles || []).map((item) => `<li><span><em class="list-kind role-kind">角色</em>${esc(item.name)}</span><span>${item.users_count} 人 · ${item.permissions_count} 权限</span></li>`)
  $('topItems').innerHTML = groups.length || roles.length ? `<ul class="mini-list">${groups.slice(0, 3).join('')}${roles.slice(0, 3).join('')}</ul>` : '<div class="panel-empty">暂无组或角色</div>'
}

function renderGroupFilter() {
  if (!has('groupFilter')) return
  const current = state.groupId
  $('groupFilter').innerHTML = '<option value="">全部用户组</option>' + state.refs.groups.map((group) => `<option value="${group.id}">${esc(group.name)} (${group.users_count ?? group.users?.length ?? 0})</option>`).join('')
  $('groupFilter').value = current
}

async function loadRefs() {
  if (!resourceKinds.includes(pageKind)) return
  const requests = []
  if (pageKind === 'users') requests.push(api('/api/v1/users/groups/?page_size=500'), api('/api/v1/rbac/roles/?page_size=500'))
  if (pageKind === 'groups') requests.push(api('/api/v1/users/users/?page_size=500'))
  if (pageKind === 'roles') requests.push(api('/api/v1/users/users/?page_size=500'), api('/api/v1/rbac/permissions/?page_size=500'))
  const values = await Promise.all(requests)
  if (pageKind === 'users') state.refs = { ...state.refs, groups: values[0].results, roles: values[1].results }
  if (pageKind === 'groups') state.refs = { ...state.refs, users: values[0].results }
  if (pageKind === 'roles') state.refs = { ...state.refs, users: values[0].results, permissions: values[1].results }
  renderGroupFilter()
}

function configureToolbar() {
  if (!resourceKinds.includes(pageKind)) return
  if (has('statusFilter')) $('statusFilter').style.display = pageKind === 'users' ? '' : 'none'
  if (has('groupFilter')) $('groupFilter').style.display = pageKind === 'users' ? '' : 'none'
  if (has('sort')) $('sort').innerHTML = pageKind === 'permissions' ? '<option value="code">按编码正序</option><option value="-code">按编码倒序</option><option value="resource">按资源</option><option value="action">按动作</option>' : '<option value="name">按名称正序</option><option value="-name">按名称倒序</option><option value="created_at">按创建时间</option><option value="-created_at">按最新创建</option>'
}

async function load() {
  if (!resourceKinds.includes(pageKind)) return
  state.search = $('search').value.trim(); state.pageSize = Number($('pageSize').value); state.sort = $('sort').value; state.status = $('statusFilter')?.value || 'all'; state.groupId = $('groupFilter')?.value || ''
  const params = new URLSearchParams({ page: state.page, page_size: state.pageSize, search: state.search, sort: state.sort })
  if (pageKind === 'users' && state.status !== 'all') params.set('is_active', state.status === 'active' ? 'true' : 'false')
  if (pageKind === 'users' && state.groupId) params.set('group_id', state.groupId)
  $('loading').textContent = '加载中…'
  try { const data = await api(`${endpoint(pageKind)}?${params}`); state.items = data.results || []; state.total = data.count || 0; state.selected.clear(); renderTable() } catch (error) { toast(error.message) } finally { $('loading').textContent = '' }
}

function renderTable() {
  const selectAll = state.items.length > 0 && state.items.every((item) => state.selected.has(item.id)); const check = `<input class="checkbox" type="checkbox" data-select-all ${selectAll ? 'checked' : ''} aria-label="全选当前页">`; let head = ''; let body = ''
  if (pageKind === 'users') { head = `<tr><th>${check}</th><th>姓名</th><th>用户名</th><th>邮箱</th><th>用户组</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th></tr>`; body = state.items.map((item) => `<tr><td><input class="checkbox" type="checkbox" data-select-id="${item.id}" ${state.selected.has(item.id) ? 'checked' : ''}></td><td>${esc(item.name)}</td><td>${esc(item.username)}</td><td>${esc(item.email) || '<span class="muted">-</span>'}</td><td>${labels(item.groups)}</td><td>${labels(item.roles)}</td><td><span class="status ${item.is_active ? 'on' : 'off'}">${item.is_active ? '启用' : '禁用'}</span></td><td class="muted">${formatDate(item.created_at)}</td><td class="actions"><button class="muted" data-action="toggle" data-id="${item.id}">${item.is_active ? '停用' : '启用'}</button><button class="muted" data-action="edit" data-id="${item.id}">编辑</button><button class="danger" data-action="delete" data-id="${item.id}">删除</button></td></tr>`).join('') }
  else if (pageKind === 'groups') { head = `<tr><th>${check}</th><th>名称</th><th>说明</th><th>成员数</th><th>创建时间</th><th>操作</th></tr>`; body = state.items.map((item) => `<tr><td><input class="checkbox" type="checkbox" data-select-id="${item.id}" ${state.selected.has(item.id) ? 'checked' : ''}></td><td>${esc(item.name)}</td><td>${esc(item.comment) || '<span class="muted">-</span>'}</td><td>${item.users_count ?? item.users?.length ?? 0}</td><td class="muted">${formatDate(item.created_at)}</td><td class="actions"><button class="success" data-action="add-all" data-id="${item.id}">加入全部用户</button><button class="muted" data-action="remove-all" data-id="${item.id}">清空成员</button><button class="muted" data-action="edit" data-id="${item.id}">编辑</button><button class="danger" data-action="delete" data-id="${item.id}">删除</button></td></tr>`).join('') }
  else if (pageKind === 'roles') { head = `<tr><th>${check}</th><th>名称</th><th>说明</th><th>权限数</th><th>成员数</th><th>创建时间</th><th>操作</th></tr>`; body = state.items.map((item) => `<tr><td><input class="checkbox" type="checkbox" data-select-id="${item.id}" ${state.selected.has(item.id) ? 'checked' : ''}></td><td>${esc(item.name)}</td><td>${esc(item.comment) || '<span class="muted">-</span>'}</td><td>${item.permissions_count ?? item.permissions?.length ?? 0}</td><td>${item.users_count ?? item.users?.length ?? 0}</td><td class="muted">${formatDate(item.created_at)}</td><td class="actions"><button class="muted" data-action="edit" data-id="${item.id}">编辑</button><button class="danger" data-action="delete" data-id="${item.id}">删除</button></td></tr>`).join('') }
  else { head = `<tr><th>${check}</th><th>编码</th><th>名称</th><th>资源</th><th>动作</th><th>创建时间</th><th>操作</th></tr>`; body = state.items.map((item) => `<tr><td><input class="checkbox" type="checkbox" data-select-id="${item.id}" ${state.selected.has(item.id) ? 'checked' : ''}></td><td><code>${esc(item.code)}</code></td><td>${esc(item.name)}</td><td>${esc(item.resource)}</td><td>${esc(item.action)}</td><td class="muted">${formatDate(item.created_at)}</td><td class="actions"><button class="muted" data-action="edit" data-id="${item.id}">编辑</button><button class="danger" data-action="delete" data-id="${item.id}">删除</button></td></tr>`).join('') }
  $('table').innerHTML = `<table><thead>${head}</thead><tbody>${body || '<tr><td colspan="9" class="empty muted">暂无数据</td></tr>'}</tbody></table>`; const pages = Math.max(1, Math.ceil((state.total || 0) / state.pageSize)); $('pager').innerHTML = `<span>共 ${state.total || 0} 条 · 第 ${state.page} / ${pages} 页</span><button class="muted" data-page="prev" ${state.page <= 1 ? 'disabled' : ''}>上一页</button><button class="muted" data-page="next" ${state.page >= pages ? 'disabled' : ''}>下一页</button>`; renderSelection()
}

function renderSelection() { const count = state.selected.size; $('selectionBar').hidden = count === 0; $('selectionInfo').textContent = `已选择 ${count} 项`; $('selectionActions').innerHTML = pageKind === 'users' ? '<button class="success" data-bulk="enable">批量启用</button><button class="muted" data-bulk="disable">批量禁用</button><button class="danger" data-bulk="delete">批量删除</button>' : '<button class="danger" data-bulk="delete">批量删除</button>' }
const checkboxGroup = (name, title, items, selected, labelKey = 'name') => `<div class="field"><label>${title}</label><div class="checks-tools"><input placeholder="筛选${title}…" data-rel-search="${name}"><button type="button" data-checks-all="${name}">全选</button><button type="button" data-checks-none="${name}">清空</button></div><div class="checks" data-checks="${name}">${items.map((item) => `<label class="check-option" data-rel-option="${name}"><input class="checkbox" type="checkbox" name="${name}" value="${item.id}" ${selected.includes(item.id) ? 'checked' : ''}> ${esc(item[labelKey] || item.name || item.code || item.username)}</label>`).join('') || '<span class="muted">暂无可选项</span>'}</div></div>`
function openForm(item = null) { state.editing = item; $('form-title').textContent = `${item ? '编辑' : '新增'}${names[pageKind]}`; let html = ''; if (pageKind === 'users') html = `<div class="field"><label>用户名</label><input name="username" required value="${esc(item?.username)}"></div><div class="field"><label>姓名</label><input name="name" required value="${esc(item?.name)}"></div><div class="field"><label>邮箱</label><input name="email" type="email" value="${esc(item?.email)}"></div><div class="field"><label>备注</label><textarea name="comment">${esc(item?.comment)}</textarea></div>${checkboxGroup('groups', '用户组', state.refs.groups, (item?.groups || []).map((x) => x.id))}${checkboxGroup('roles', '角色', state.refs.roles, (item?.roles || []).map((x) => x.id))}`; if (pageKind === 'groups') html = `<div class="field"><label>名称</label><input name="name" required value="${esc(item?.name)}"></div><div class="field"><label>说明</label><textarea name="comment">${esc(item?.comment)}</textarea></div>${checkboxGroup('users', '成员', state.refs.users, item?.users || [])}`; if (pageKind === 'roles') html = `<div class="field"><label>名称</label><input name="name" required value="${esc(item?.name)}"></div><div class="field"><label>说明</label><textarea name="comment">${esc(item?.comment)}</textarea></div>${checkboxGroup('permissions', '权限', state.refs.permissions, item?.permissions || [], 'code')}${checkboxGroup('users', '成员', state.refs.users, item?.users || [])}`; if (pageKind === 'permissions') html = `<div class="field"><label>编码</label><input name="code" required value="${esc(item?.code)}"></div><div class="field"><label>名称</label><input name="name" required value="${esc(item?.name)}"></div><div class="field"><label>资源</label><input name="resource" value="${esc(item?.resource)}"></div><div class="field"><label>动作</label><input name="action" value="${esc(item?.action)}"></div>`; $('form').innerHTML = `${html}<div class="dialog-actions"><button type="button" data-action="close-form">取消</button><button class="primary" type="submit">保存 <span class="muted">(Ctrl+Enter)</span></button></div>`; $('modal').classList.add('open'); $('form').querySelector('input')?.focus() }
function closeForm() { $('modal').classList.remove('open'); state.editing = null }
function formData() { const data = Object.fromEntries(new FormData($('form')).entries()); for (const name of ['groups', 'roles', 'users', 'permissions']) data[name] = [...$('form').querySelectorAll(`input[name="${name}"]:checked`)].map((input) => Number(input.value)); return data }
async function refreshResource() { await Promise.all([loadSummary(), loadRefs(), load()]) }
async function saveForm(event) { event.preventDefault(); const base = endpoint(pageKind); try { await api(state.editing ? `${base}${state.editing.id}/` : base, { method: state.editing ? 'PATCH' : 'POST', body: JSON.stringify(formData()) }); closeForm(); toast('保存成功'); await refreshResource() } catch (error) { toast(error.message) } }
async function deleteItems(ids) { if (!ids.length || !confirm(`确定删除选中的 ${ids.length} 项吗？关联关系也会同步删除。`)) return; try { await api(`${endpoint(pageKind)}?ids=${ids.join(',')}`, { method: 'DELETE' }); toast(`已删除 ${ids.length} 项`); await refreshResource() } catch (error) { toast(error.message) } }
async function bulkAction(action) { const ids = selectedIds(); if (!ids.length) return; if (action === 'delete') return deleteItems(ids); try { await api('/api/v1/users/users/', { method: 'PATCH', body: JSON.stringify(ids.map((id) => ({ id, is_active: action === 'enable' }))) }); toast(action === 'enable' ? '已批量启用' : '已批量禁用'); await refreshResource() } catch (error) { toast(error.message) } }
async function toggleUser(item) { try { await api(`/api/v1/users/users/${item.id}/status/`, { method: 'PATCH', body: JSON.stringify({ is_active: !item.is_active }) }); toast(item.is_active ? '用户已停用' : '用户已启用'); await refreshResource() } catch (error) { toast(error.message) } }
async function addAllUsers(id) { if (!confirm('确定把全部用户加入该用户组吗？')) return; try { const result = await api(`/api/v1/users/groups/${id}/add-all-users/`, { method: 'POST' }); toast(`用户组现有 ${result.count} 名成员`); await refreshResource() } catch (error) { toast(error.message) } }
async function removeAllUsers(id) { if (!confirm('确定清空该用户组的全部成员吗？')) return; try { const result = await api(`/api/v1/users/groups/${id}/remove-all-users/`, { method: 'POST' }); toast(`已移除 ${result.count} 名成员`); await refreshResource() } catch (error) { toast(error.message) } }
function changePage(direction) { const pages = Math.max(1, Math.ceil((state.total || 0) / state.pageSize)); state.page = Math.min(pages, Math.max(1, state.page + direction)); load() }
function clearFilters() { $('search').value = ''; if (has('statusFilter')) $('statusFilter').value = 'all'; if (has('groupFilter')) $('groupFilter').value = ''; state.page = 1; load() }

function bindResourceEvents() {
  configureToolbar()
  $('table').addEventListener('change', (event) => { if (event.target.dataset.selectId) { const id = Number(event.target.dataset.selectId); event.target.checked ? state.selected.add(id) : state.selected.delete(id); renderSelection() } if (event.target.dataset.selectAll) { state.items.forEach((item) => event.target.checked ? state.selected.add(item.id) : state.selected.delete(item.id)); renderTable() } })
  $('table').addEventListener('click', async (event) => { const button = event.target.closest('[data-action]'); if (!button) return; const id = Number(button.dataset.id); const item = state.items.find((entry) => entry.id === id); if (button.dataset.action === 'edit') openForm(item); if (button.dataset.action === 'delete') await deleteItems([id]); if (button.dataset.action === 'toggle') await toggleUser(item); if (button.dataset.action === 'add-all') await addAllUsers(id); if (button.dataset.action === 'remove-all') await removeAllUsers(id) })
  $('selectionActions').addEventListener('click', (event) => { const button = event.target.closest('[data-bulk]'); if (button) bulkAction(button.dataset.bulk) }); $('pager').addEventListener('click', (event) => { const button = event.target.closest('[data-page]'); if (button) changePage(button.dataset.page === 'next' ? 1 : -1) })
  $('form').addEventListener('submit', saveForm); $('form').addEventListener('click', (event) => { if (event.target.closest('[data-action="close-form"]')) closeForm(); const all = event.target.closest('[data-checks-all]'); const none = event.target.closest('[data-checks-none]'); if (all || none) { const name = (all || none).dataset.checksAll || (all || none).dataset.checksNone; $('form').querySelectorAll(`input[name="${name}"]`).forEach((input) => { input.checked = Boolean(all) }) } }); $('form').addEventListener('input', (event) => { const input = event.target.closest('[data-rel-search]'); if (!input) return; const query = input.value.trim().toLowerCase(); $('form').querySelectorAll(`[data-rel-option="${input.dataset.relSearch}"]`).forEach((option) => { option.classList.toggle('is-hidden', query && !option.textContent.toLowerCase().includes(query)) }) }); $('modal').addEventListener('click', (event) => { if (event.target === $('modal')) closeForm() })
  $('add').onclick = () => openForm(); $('refresh').onclick = refreshResource; $('clearFilters').onclick = clearFilters; $('export').onclick = () => { const params = new URLSearchParams({ kind: pageKind, search: state.search }); if (pageKind === 'users' && state.status !== 'all') params.set('is_active', state.status === 'active' ? 'true' : 'false'); if (pageKind === 'users' && state.groupId) params.set('group_id', state.groupId); window.location.href = `/api/export?${params}` }
  $('search').oninput = () => { clearTimeout(window.searchTimer); window.searchTimer = setTimeout(() => { state.page = 1; load() }, 250) }; $('statusFilter').onchange = () => { state.page = 1; load() }; $('groupFilter').onchange = () => { state.page = 1; load() }; $('sort').onchange = () => { state.page = 1; load() }; $('pageSize').onchange = () => { state.page = 1; load() }
  document.addEventListener('keydown', (event) => { if (event.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') { event.preventDefault(); $('search').focus() } if (event.key.toLowerCase() === 'n' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') openForm(); if (event.key === 'Escape') closeForm(); if (event.ctrlKey && event.key === 'Enter' && $('modal').classList.contains('open')) $('form').requestSubmit() })
}

async function init() {
  setupHeader(); await loadSummary()
  if (pageKind === 'dashboard') { await loadInsights(); if (has('refreshInsights')) $('refreshInsights').onclick = loadInsights; return }
  bindResourceEvents()
  const query = new URLSearchParams(window.location.search)
  if (pageKind === 'users' && query.get('status') === 'active') $('statusFilter').value = 'active'
  await loadRefs(); await load()
}
init().catch((error) => toast(error.message))
