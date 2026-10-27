import { test, expect, type Page } from '@playwright/test'

const rid = '00000000-0000-4000-8000-000000000404'
const privateDetail = 'PRIVATE_TEST_SQL SELECT secret FROM private_data'
const headers = { 'X-PaperPilot-Client': 'web' }
const fixture = { name: 'business.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nB integration fixture') }

async function login(page: Page, username = 'alice') {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '开发演示登录' })).toBeVisible()
  if (username !== 'alice') {
    await page.getByRole('combobox').press('ArrowDown')
    await page.getByRole('option', { name: username, exact: true }).click()
  }
  await page.getByRole('button', { name: '进入演示' }).click()
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
}
async function project(page: Page) {
  await login(page)
  const name = `B集成-${test.info().testId}-${Date.now()}`
  await page.getByLabel('新项目名称').fill(name)
  await page.getByRole('button', { name: '创建项目' }).click()
  await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
  return { id: new URL(page.url()).pathname.split('/')[2]!, name }
}
async function research(page: Page) {
  await page.getByRole('link', { name: '研究', exact: true }).click()
  await expect(page.getByRole('heading', { name: '研究工作台' })).toBeVisible()
}
async function upload(page: Page) {
  await page.getByLabel('选择 PDF 文件').setInputFiles(fixture)
  await page.getByRole('button', { name: '上传并模拟处理' }).click()
  await expect(page.getByRole('region', { name: '任务恢复' }).getByText(/任务：succeeded/)).toBeVisible()
}

for (const resource of ['projects', 'papers', 'reports', 'audit'] as const) {
  test(`${resource}: loading and failure are distinct from empty; retry only rereads`, async ({ page }) => {
    const current = await project(page)
    if (resource === 'projects') await page.getByRole('link', { name: '← 全部项目' }).click()
    const path = resource === 'projects' ? '/api/v1/projects' : `/api/v1/projects/${current.id}/${resource === 'audit' ? 'audit-events' : resource}`
    const empty = { projects: '尚无项目，可以创建一个项目开始', papers: '当前范围内没有文献', reports: '尚未保存报告', audit: '当前筛选条件下没有审计事件' }[resource]
    const retry = { projects: '重新加载项目', papers: '重新加载文献', reports: '重新加载报告', audit: '重新加载审计' }[resource]
    let calls = 0, release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    await page.route('**/api/v1/**', async route => {
      if (route.request().method() !== 'GET' || new URL(route.request().url()).pathname !== path) return route.continue()
      calls++
      if (calls !== 1) return route.continue()
      await gate
      await route.fulfill({ status: 503, headers: { 'X-Request-ID': rid }, contentType: 'application/json', body: JSON.stringify({ code: 'DATA_UNAVAILABLE', message: privateDetail, request_id: 'invalid-id' }) })
    })
    if (resource === 'projects') await page.getByRole('button', { name: '刷新项目' }).click()
    if (resource === 'papers') await page.getByRole('button', { name: '筛选', exact: true }).click()
    if (resource === 'reports') await research(page)
    if (resource === 'audit') await page.getByRole('link', { name: '查询与审计', exact: true }).click()
    await expect.poll(() => calls).toBe(1)
    await expect(page.getByRole('status').filter({ hasText: '正在处理' }).first()).toBeVisible()
    await expect(page.getByText(empty, { exact: true })).not.toBeVisible()
    release()
    await expect(page.getByRole('alert').filter({ hasText: 'DATA_UNAVAILABLE' })).toContainText(rid)
    await expect(page.getByText(empty, { exact: true })).not.toBeVisible()
    await expect(page.getByText(privateDetail, { exact: false })).not.toBeVisible()
    expect(calls).toBe(1)
    await page.getByRole('button', { name: retry, exact: true }).click()
    await expect.poll(() => calls).toBe(2)
    await expect(page.getByRole('alert').filter({ hasText: 'DATA_UNAVAILABLE' })).not.toBeVisible()
    if (resource === 'papers' || resource === 'reports') await expect(page.getByText(empty, { exact: true })).toBeVisible()
    if (resource === 'projects') await expect(page.locator('.project-card').filter({ hasText: current.name })).toBeVisible()
    if (resource === 'audit') await expect(page.getByText('project.created', { exact: true })).toBeVisible()
  })
}

test('startup service failure can reconnect without reloading the app', async ({ page }) => {
  let calls = 0
  await page.route('**/api/v1/health', route => {
    calls++
    return calls === 1 ? route.fulfill({ status: 503, headers: { 'X-Request-ID': rid }, contentType: 'text/html', body: privateDetail }) : route.continue()
  })
  await page.goto('/')
  await expect(page.getByRole('button', { name: '重新连接服务' })).toBeVisible()
  await expect(page.getByRole('alert').filter({ hasText: 'REQUEST_FAILED' })).toContainText(rid)
  await page.getByRole('button', { name: '重新连接服务' }).click()
  await expect(page.getByText('MOCK · 开发演示', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '重新连接服务' })).not.toBeVisible()
  expect(calls).toBe(2)
  await expect(page.getByText(privateDetail)).not.toBeVisible()
})

test('logout failure is visible and does not claim the session was closed', async ({ page }) => {
  await login(page)
  await page.route('**/api/v1/auth/logout', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'INTERNAL_ERROR', message: privateDetail, request_id: rid }) }))
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'INTERNAL_ERROR' })).toContainText(rid)
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
  await expect(page.getByText(privateDetail)).not.toBeVisible()
  await page.unroute('**/api/v1/auth/logout')
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page.getByRole('heading', { name: '开发演示登录' })).toBeVisible()
})

test('401 retains request ID and removes protected project UI without resubmitting', async ({ page }) => {
  const current = await project(page)
  await page.route(`**/api/v1/projects/${current.id}/papers`, route => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ code: 'UNAUTHENTICATED', message: privateDetail, request_id: rid }) }))
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.getByRole('heading', { name: '开发演示登录' })).toBeVisible()
  await expect(page.getByRole('alert').filter({ hasText: '登录已失效' })).toContainText(rid)
  await expect(page.getByRole('heading', { name: '文献工作台' })).not.toBeVisible()
  await expect(page.getByText(current.name, { exact: true })).not.toBeVisible()
  await expect(page.getByText(privateDetail)).not.toBeVisible()
})

test('client differentiates network loss and actual abort timeout; no automatic retry', async ({ page }) => {
  const current = await project(page)
  let calls = 0
  await page.route('**/api/v1/health', route => { calls++; return route.abort('internetdisconnected') })
  const network = await page.evaluate(async () => {
    const modulePath = '/src/api/client.ts', { api } = await import(modulePath)
    try { await api.health(); return 'unexpected-success' } catch (error) { return (error as { code: string }).code }
  })
  expect(network).toBe('NETWORK_ERROR'); expect(calls).toBe(1)
  await page.route('**/tasks/*', () => { /* Deliberately stall a read until its signal expires. */ })
  const timeout = await page.evaluate(async id => {
    const modulePath = '/src/api/client.ts', { api } = await import(modulePath)
    try { await api.task(id, '00000000-0000-4000-8000-000000000123', AbortSignal.timeout(10)); return 'unexpected-success' } catch (error) { return (error as { code: string }).code }
  }, current.id)
  expect(timeout).toBe('REQUEST_TIMEOUT')
})

test('client rejects malformed success rather than returning fabricated data', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '开发演示登录' })).toBeVisible()
  await page.route('**/api/v1/health', route => route.fulfill({ status: 200, contentType: 'application/json', body: 'null' }))
  const result = await page.evaluate(async () => {
    const modulePath = '/src/api/client.ts', { api } = await import(modulePath)
    try { await api.health(); return 'unexpected-success' } catch (error) { return (error as { code: string }).code }
  })
  expect(result).toBe('INVALID_RESPONSE')
})

test('normal and business-rejected responses preserve their actual semantics', async ({ page }) => {
  await login(page)
  const result = await page.evaluate(async () => {
    const modulePath = '/src/api/client.ts', { api } = await import(modulePath)
    const health = await api.health()
    try { await api.createProject(''); return { health, code: 'unexpected-success', status: 0, requestId: '' } }
    catch (error) { const e = error as { code: string; status: number; requestId: string }; return { health, code: e.code, status: e.status, requestId: e.requestId } }
  })
  expect(result.health.modes).toEqual({ a: 'mock', c: 'mock', d: 'mock' })
  expect(result.code).toBe('VALIDATION_ERROR'); expect(result.status).toBe(422)
  expect(result.requestId).toMatch(/^[0-9a-f-]{36}$/)
})

test('AI search failure leaves saved reports readable without generating tasks', async ({ page }) => {
  await project(page); await research(page)
  await page.getByLabel('报告标题').fill('独立读取报告')
  await page.getByLabel('报告正文').fill('不依赖 AI 的已保存正文')
  await page.getByRole('button', { name: '保存新版本' }).click()
  await expect(page.getByText(/已保存 Mock 版本 1/)).toBeVisible()
  let creates = 0
  page.on('request', request => { if (request.method() === 'POST' && /\/(answers|report-drafts|evidence)$/.test(new URL(request.url()).pathname)) creates++ })
  await page.route('**/search', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'AI_UNAVAILABLE', message: privateDetail, request_id: rid }) }))
  await page.getByRole('button', { name: '检索', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'AI_UNAVAILABLE' })).toContainText(rid)
  await page.getByRole('button', { name: '独立读取报告 · v1' }).click()
  await expect(page.getByLabel('报告正文')).toHaveValue('不依赖 AI 的已保存正文')
  expect(creates).toBe(0)
})

test('acknowledged save stays saved when version refresh fails; retry only GETs', async ({ page }) => {
  await project(page); await research(page)
  let saves = 0
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/reports')) saves++ })
  await page.route('**/reports/*/versions', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'DATA_UNAVAILABLE', message: privateDetail, request_id: rid }) }))
  await page.getByLabel('报告正文').fill('已确认保存的正文')
  await page.getByRole('button', { name: '保存新版本' }).click()
  await expect(page.getByText(/已保存 Mock 版本 1/)).toBeVisible()
  await expect(page.getByRole('button', { name: '重新加载版本' })).toBeVisible()
  expect(saves).toBe(1)
  await page.unroute('**/reports/*/versions')
  await page.getByRole('button', { name: '重新加载版本' }).click()
  await expect(page.getByRole('button', { name: '版本 1', exact: true })).toBeVisible()
  expect(saves).toBe(1)
  await expect(page.getByRole('button', { name: '版本 2', exact: true })).not.toBeVisible()
})

test('paper removal requires confirmation and retains the saved report snapshot', async ({ page }) => {
  const current = await project(page); await upload(page); await research(page)
  await page.getByRole('button', { name: '生成 Mock 草稿' }).click()
  await expect(page.getByLabel('报告正文')).toHaveValue(/Mock 合成内容/)
  await page.getByRole('button', { name: '保存新版本' }).click()
  await expect(page.getByText(/已保存 Mock 版本 1/)).toBeVisible()
  await page.getByRole('link', { name: '文献', exact: true }).click()
  await expect(page.getByRole('button', { name: '移出文献 business' })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  const deletes: string[] = []
  page.on('request', request => { if (request.method() === 'DELETE') deletes.push(request.url()) })
  await page.getByRole('button', { name: '移出文献 business' }).click()
  await expect(page.getByText('移出后不再参与检索；历史报告保留引用快照。确认移出？')).toBeVisible()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  expect(deletes).toHaveLength(0)
  await page.getByRole('button', { name: '移出文献 business' }).click()
  await page.getByRole('button', { name: '确认移出', exact: true }).click()
  await expect(page.getByText('当前范围内没有文献')).toBeVisible()
  expect(deletes).toHaveLength(1)
  expect(new URL(deletes[0]!).searchParams.get('confirm_source_impact')).toBe('true')
  expect((await page.request.get(deletes[0]!)).status()).toBe(404)
  await research(page)
  await page.getByRole('button', { name: '研究综述草稿 · v1' }).click()
  await expect(page.getByLabel('报告正文')).toHaveValue(/Mock 合成内容/)
  await page.getByRole('button', { name: '引用清单', exact: true }).click()
  await expect(page.getByText('[1] 第 1 页 · 合成来源，待核验', { exact: true })).toBeVisible()
  expect(new URL(page.url()).pathname).toContain(current.id)
})

test('member has no remove control and server still rejects a direct confirmed removal', async ({ page }) => {
  const current = await project(page); await upload(page)
  const papers = await (await page.request.get(`/api/v1/projects/${current.id}/papers`)).json()
  const path = `/api/v1/projects/${current.id}/papers/${papers.items[0].paper_id}?confirm_source_impact=true`
  expect((await page.request.post(`/api/v1/projects/${current.id}/members`, { headers, data: { username: 'bob' } })).status()).toBe(201)
  await page.getByRole('button', { name: '退出', exact: true }).click()
  // A click starts the async logout; wait for its acknowledged UI before testing the anonymous request.
  await expect(page.getByRole('heading', { name: '开发演示登录' })).toBeVisible()
  expect((await page.request.delete(path, { headers })).status()).toBe(401)
  await login(page, 'bob')
  await page.locator('.project-card').filter({ hasText: current.name }).getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
  await expect(page.getByRole('button', { name: /移出文献/ })).toHaveCount(0)
  const denied = await page.request.delete(path, { headers })
  expect(denied.status()).toBe(403)
  expect(await denied.text()).not.toContain('business')
  expect((await page.request.get(path)).status()).toBe(200)
})

test('registered-mode display remains unverified and honors returned Mock provenance', async ({ page }) => {
  await page.route('**/api/v1/health', async route => {
    const response = await route.fetch(), data = await response.json()
    // Browser display fixture only: no real module or production capability is asserted.
    await route.fulfill({ response, json: { ...data, modes: { a: 'persistent', c: 'real', d: 'real' } } })
  })
  await project(page)
  await expect(page.getByText('适配器开发 · 待验收', { exact: true })).toBeVisible()
  await expect(page.getByText(/持久化和重启恢复需由 A 实际验收/)).toBeVisible()
  await expect(page.getByText(/数据将在重启后保留/)).not.toBeVisible()
  await research(page)
  await page.getByRole('button', { name: '生成回答', exact: true }).click()
  await expect(page.getByRole('heading', { name: '证据问答 Mock' })).toBeVisible()
  await expect(page.getByText('当前范围内证据不足', { exact: true })).toBeVisible()
})

test('gateway rejection shows safe reason and request ID instead of provider internals', async ({ page }) => {
  await project(page)
  await page.getByRole('link', { name: '查询与审计', exact: true }).click()
  await page.route('**/secure-queries', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ decision: 'rejected', execution_status: 'not_executed', columns: [], rows: [], reason: { code: 'QUERY_BLOCKED', message: privateDetail }, policy_id: 'browser-fixture', request_id: rid, mock: true }) }))
  await page.getByRole('button', { name: '提交 Mock 查询' }).click()
  await expect(page.getByText('rejected / not_executed', { exact: true })).toBeVisible()
  await expect(page.getByText(rid, { exact: true })).toBeVisible()
  await expect(page.getByRole('alert').filter({ hasText: 'QUERY_BLOCKED' })).toContainText('查询未完成或被网关拒绝')
  await expect(page.getByText(privateDetail)).not.toBeVisible()
})

test('failed task displays a safe reason and request ID without changing recovery behavior', async ({ page }) => {
  await project(page); await research(page)
  let creates = 0
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/answers')) creates++ })
  await page.route('**/tasks/*', async route => {
    const response = await route.fetch(), task = await response.json()
    await route.fulfill({ response, json: { ...task, status: 'failed', result: null, result_ref: null, request_id: rid, error: { code: 'AI_UNAVAILABLE', message: privateDetail } } })
  })
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  const panel = page.getByRole('region', { name: '任务恢复' })
  await expect(panel.getByText(/任务：failed/)).toBeVisible()
  await expect(panel.getByText(/AI_UNAVAILABLE/)).toContainText(rid)
  await expect(page.getByText(privateDetail)).not.toBeVisible()
  const taskId = await page.getByTestId('recovery-task-id').innerText()
  await page.reload()
  await expect(page.getByTestId('recovery-task-id')).toHaveText(taskId)
  await expect(panel.getByText(/AI_UNAVAILABLE/)).toContainText(rid)
  expect(creates).toBe(1)
})

test('paper detail failure can retry the original paper and reveal its source', async ({ page }) => {
  const current = await project(page); await upload(page)
  const papers = await (await page.request.get(`/api/v1/projects/${current.id}/papers`)).json()
  const path = `/api/v1/projects/${current.id}/papers/${papers.items[0].paper_id}`
  let calls = 0
  await page.route(`**${path}`, route => {
    calls++
    return calls === 1 ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'DATA_UNAVAILABLE', message: privateDetail, request_id: rid }) }) : route.continue()
  })
  await page.getByRole('button', { name: '详情', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '论文详情与来源' })
  await expect(drawer.getByRole('alert')).toContainText(rid)
  await expect(drawer.getByRole('heading', { name: 'business', exact: true })).not.toBeVisible()
  await drawer.getByRole('button', { name: '重新加载详情' }).click()
  await expect(drawer.getByRole('heading', { name: 'business', exact: true })).toBeVisible()
  await drawer.getByText('[1] 第 1 页 · 合成来源，待核验', { exact: true }).click()
  await expect(drawer.locator('.sources p')).toContainText('合成')
  expect(calls).toBe(2)
})

test('member dialog retries failed reads and performs normal add and remove', async ({ page }) => {
  const current = await project(page)
  await page.getByRole('link', { name: '← 全部项目' }).click()
  let reads = 0, adds = 0, removes = 0
  await page.route(`**/api/v1/projects/${current.id}/members*`, route => {
    if (route.request().method() !== 'GET') return route.continue()
    reads++
    return reads === 1 ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'DATA_UNAVAILABLE', message: privateDetail, request_id: rid }) }) : route.continue()
  })
  page.on('request', request => {
    const path = new URL(request.url()).pathname
    if (request.method() === 'POST' && path.endsWith('/members')) adds++
    if (request.method() === 'DELETE' && path.includes('/members/')) removes++
  })
  await page.locator('.project-card').filter({ hasText: current.name }).getByRole('button', { name: '管理成员' }).click()
  const dialog = page.getByRole('dialog', { name: '项目成员' })
  await expect(dialog.getByRole('alert')).toContainText(rid)
  await dialog.getByRole('button', { name: '重新加载成员' }).click()
  await expect(dialog.getByRole('cell', { name: 'alice', exact: true })).toBeVisible()
  await dialog.getByLabel('添加成员账户').fill('bob')
  await dialog.getByRole('button', { name: '添加成员', exact: true }).click()
  const member = dialog.getByRole('row').filter({ hasText: 'bob' })
  await expect(member).toBeVisible()
  await member.getByRole('button', { name: '移除', exact: true }).click()
  await expect(member).not.toBeVisible()
  await expect(dialog.getByRole('cell', { name: 'alice', exact: true })).toBeVisible()
  expect(adds).toBe(1); expect(removes).toBe(1); expect(reads).toBe(4)
})
