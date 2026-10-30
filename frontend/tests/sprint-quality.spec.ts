import { test, expect, type Page } from '@playwright/test'

async function project(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
  await page.getByLabel('新项目名称').fill(`冲刺验收-${Date.now()}`)
  await page.getByLabel('新项目名称').press('Enter')
  await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
}
const pdf = { name: 'sprint.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nSprint fixture only') }

test('project form locates whitespace validation and keyboard submission works', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  const field = page.getByLabel('新项目名称')
  await field.fill('   ')
  await field.press('Enter')
  await expect(page.getByRole('alert').filter({ hasText: '请输入项目名称' })).toBeVisible()
  await expect(field).toHaveAttribute('aria-invalid', 'true')
  await field.fill(`键盘项目-${Date.now()}`)
  await field.press('Enter')
  await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
})

test('invalid or oversized upload never creates a task and valid replacement recovers', async ({ page }) => {
  await project(page)
  let creates = 0
  page.on('request', request => { if (request.method() === 'POST' && /\/papers$/.test(new URL(request.url()).pathname)) creates++ })
  await page.getByLabel('选择 PDF 文件').setInputFiles({ ...pdf, buffer: Buffer.alloc(0) })
  await expect(page.getByRole('alert').filter({ hasText: '文件为空' })).toBeVisible()
  await page.getByLabel('选择 PDF 文件').setInputFiles({ ...pdf, buffer: Buffer.from('not a PDF') })
  await expect(page.getByRole('alert').filter({ hasText: '文件头不是 PDF' })).toBeVisible()
  await expect(page.getByRole('button', { name: '上传并模拟处理' })).toBeDisabled()
  await page.getByLabel('选择 PDF 文件').setInputFiles({ ...pdf, buffer: Buffer.alloc(20 * 1024 * 1024 + 1) })
  await expect(page.getByRole('alert').filter({ hasText: '单篇文件上限' })).toBeVisible()
  expect(creates).toBe(0)
  await page.getByLabel('选择 PDF 文件').setInputFiles(pdf)
  await page.getByRole('button', { name: '上传并模拟处理' }).click()
  await expect(page.getByRole('region', { name: '任务恢复' })).toContainText('任务：succeeded')
  expect(creates).toBe(1)
  await expect(page.getByText('可检索', { exact: true })).toBeVisible()
})

test('reversed year range blocks only the invalid filter without sending a request', async ({ page }) => {
  await project(page)
  let reads = 0
  page.on('request', request => { if (request.method() === 'GET' && /\/papers$/.test(new URL(request.url()).pathname)) reads++ })
  await page.getByLabel('起始年份', { exact: true }).fill('2025')
  await page.getByLabel('结束年份', { exact: true }).fill('2020')
  await expect(page.getByText('起始年份不能晚于结束年份。')).toBeVisible()
  await expect(page.getByRole('button', { name: '筛选', exact: true })).toBeDisabled()
  expect(reads).toBe(0)
  await page.getByLabel('结束年份', { exact: true }).fill('2026')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect.poll(() => reads).toBe(1)
  await page.getByRole('link', { name: '研究', exact: true }).click()
  await page.getByLabel('检索起始年份').fill('2025')
  await page.getByLabel('检索结束年份').fill('2020')
  await expect(page.getByRole('button', { name: '检索', exact: true })).toBeDisabled()
  await expect(page.getByText('起始年份不能晚于结束年份。')).toBeVisible()
})

test('late generated draft preserves edits and explicit apply reads the existing result', async ({ page }) => {
  await project(page)
  await page.getByRole('link', { name: '研究', exact: true }).click()
  let release!: () => void, queries = 0, creates = 0
  const gate = new Promise<void>(resolve => { release = resolve })
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/report-drafts')) creates++ })
  await page.route('**/tasks/*', async route => { if (route.request().method() !== 'GET') return route.continue(); queries++; await gate; await route.continue() })
  await page.getByRole('button', { name: '生成 Mock 草稿' }).click()
  await expect.poll(() => queries).toBe(1)
  await page.getByLabel('报告正文').fill('生成期间手工写下的研究内容')
  release()
  await expect(page.getByText('新草稿已完成；生成期间你修改了编辑内容，已保留当前编辑。')).toBeVisible()
  await expect(page.getByLabel('报告正文')).toHaveValue('生成期间手工写下的研究内容')
  await page.getByRole('button', { name: '载入生成草稿' }).click()
  await expect(page.getByLabel('报告正文')).toHaveValue(/Mock 合成内容/)
  expect(creates).toBe(1)
})

for (const sample of ['按年份统计论文数量', '模拟执行失败', '不支持的问题']) {
  test(`query state explains ${sample} and clearing never resubmits`, async ({ page }) => {
    await project(page)
    await page.getByRole('link', { name: '查询与审计', exact: true }).click()
    await page.getByLabel('统计问题').fill(sample)
    let creates = 0
    page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/secure-queries')) creates++ })
    await page.getByRole('button', { name: '提交 Mock 查询' }).click()
    const message = sample === '按年份统计论文数量' ? '演示结果已返回，未执行真实 SQL' : sample === '模拟执行失败' ? '查询执行失败，没有可用结果' : '查询被策略拒绝，未执行'
    await expect(page.getByText(message, { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '清除查询结果' }).click()
    await expect(page.getByText('输入统计问题后提交查询')).toBeVisible()
    expect(creates).toBe(1)
  })
}

for (const width of [390, 768, 1440]) {
  test(`four pages at ${width}px retain keyboard access and contain long content`, async ({ page }) => {
    await page.setViewportSize({ width, height: 960 })
    const exceptions: string[] = [], consoleErrors: string[] = []
    page.on('pageerror', error => exceptions.push(error.message))
    await project(page)
    // Start console checking after the expected unauthenticated startup request.
    page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
    await page.getByRole('link', { name: '← 全部项目' }).click()
    await page.keyboard.press('Tab')
    await page.getByRole('link', { name: '跳到主要内容' }).focus()
    await page.keyboard.press('Enter')
    await expect(page.locator('#main-content')).toBeFocused()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../output/sprint-20261026/after/${width}-projects.png`, fullPage: true, animations: 'disabled' })
    await page.locator('.project-card').last().getByRole('button', { name: '进入工作台' }).click()
    await page.getByLabel('选择 PDF 文件').setInputFiles(pdf)
    await page.getByLabel('论文标题').fill('长论文标题与证据关联'.repeat(15))
    await page.getByRole('button', { name: '上传并模拟处理' }).click()
    await expect(page.getByRole('region', { name: '任务恢复' })).toContainText('任务：succeeded')
    await expect(page.getByRole('cell', { name: '可检索', exact: true })).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../output/sprint-20261026/after/${width}-papers.png`, fullPage: true, animations: 'disabled' })
    const details = page.getByRole('button', { name: '详情', exact: true }).first()
    await details.scrollIntoViewIfNeeded()
    await details.click()
    await expect(page.getByRole('heading', { name: '长论文标题与证据关联'.repeat(15), exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Close this dialog' }).click()
    await page.getByRole('link', { name: '研究', exact: true }).click()
    await page.getByRole('button', { name: '检索', exact: true }).click()
    await expect(page.getByText('1 条结果', { exact: true })).toBeVisible()
    await page.getByLabel('报告正文').fill('long_unbroken_text_'.repeat(100))
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../output/sprint-20261026/after/${width}-research.png`, fullPage: true, animations: 'disabled' })
    await page.getByRole('link', { name: '查询与审计', exact: true }).click()
    await expect(page.getByRole('dialog', { name: '离开前处理未保存编辑' })).toBeVisible()
    await page.getByRole('button', { name: '放弃未保存编辑并继续' }).click()
    await page.getByRole('button', { name: '提交 Mock 查询' }).click()
    await expect(page.getByText('演示结果已返回，未执行真实 SQL', { exact: true })).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../output/sprint-20261026/after/${width}-security.png`, fullPage: true, animations: 'disabled' })
    expect(exceptions).toEqual([])
    expect(consoleErrors).toEqual([])
  })
}

test('incomplete health payload shows reconnect and preserves request ID without crashing', async ({ page }) => {
  const id = '00000000-0000-4000-8000-000000000405', exceptions: string[] = []
  page.on('pageerror', error => exceptions.push(error.message))
  await page.route('**/api/v1/health', route => route.fulfill({ status: 200, headers: { 'X-Request-ID': id }, contentType: 'application/json', body: '{}' }))
  await page.goto('/')
  await expect(page.getByRole('alert').filter({ hasText: 'INVALID_RESPONSE' })).toContainText(id)
  await expect(page.getByText('运行模式未知', { exact: true })).toBeVisible()
  await page.unroute('**/api/v1/health')
  await page.getByRole('button', { name: '重新连接服务' }).click()
  await expect(page.getByText('MOCK · 开发演示', { exact: true })).toBeVisible()
  expect(exceptions).toEqual([])
})

test('stalled response body retains timeout semantics and its response request ID', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '开发演示登录' })).toBeVisible()
  const result = await page.evaluate(async () => {
    const modulePath = '/src/api/client.ts', { api } = await import(modulePath)
    const originalFetch = window.fetch, originalTimeout = AbortSignal.timeout
    AbortSignal.timeout = () => originalTimeout(25)
    window.fetch = async request => {
      const signal = (request as Request).signal
      return new Response(new ReadableStream({ start(controller) {
        signal.addEventListener('abort', () => controller.error(signal.reason), { once: true })
      } }), { status: 200, headers: { 'X-Request-ID': '00000000-0000-4000-8000-000000000406' } })
    }
    try { await api.health(); return { code: 'unexpected-success', requestId: '' } }
    catch (error) { const e = error as { code: string; requestId: string }; return { code: e.code, requestId: e.requestId } }
    finally { window.fetch = originalFetch; AbortSignal.timeout = originalTimeout }
  })
  expect(result).toEqual({ code: 'REQUEST_TIMEOUT', requestId: '00000000-0000-4000-8000-000000000406' })
})

test('HTTP 200 with an error envelope cannot be treated as a successful mutation', async ({ page }) => {
  await project(page)
  await page.getByRole('link', { name: '← 全部项目' }).click()
  await page.route('**/api/v1/projects', route => route.request().method() === 'POST'
    ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 'DATA_UNAVAILABLE', message: 'PRIVATE_PROVIDER_DETAIL', request_id: '00000000-0000-4000-8000-000000000407' }) }) : route.continue())
  await page.getByLabel('新项目名称').fill('不能伪造成功')
  await page.getByRole('button', { name: '创建项目' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'INVALID_RESPONSE' })).toContainText('00000000-0000-4000-8000-000000000407')
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
  await expect(page.getByLabel('新项目名称')).toHaveValue('不能伪造成功')
  await expect(page.getByText('PRIVATE_PROVIDER_DETAIL')).not.toBeVisible()
})

test('report rejection keeps edits and an acknowledged version shows subsequent unsaved changes', async ({ page }) => {
  await project(page)
  await page.getByRole('link', { name: '研究', exact: true }).click()
  await page.getByLabel('报告正文').fill('服务故障时必须保留的编辑')
  let saves = 0
  await page.route('**/reports', route => {
    if (route.request().method() !== 'POST') return route.continue()
    saves++
    return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'DATA_UNAVAILABLE', message: 'PRIVATE_SAVE_DETAIL', request_id: '00000000-0000-4000-8000-000000000408' }) })
  })
  await page.getByRole('button', { name: '保存新版本' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'DATA_UNAVAILABLE' })).toBeVisible()
  await expect(page.getByLabel('报告正文')).toHaveValue('服务故障时必须保留的编辑')
  await expect(page.getByText('有未保存的编辑', { exact: true })).toBeVisible()
  expect(saves).toBe(1)
  await page.unroute('**/reports')
  await page.getByRole('button', { name: '保存新版本' }).click()
  await expect(page.getByText(/已保存 Mock 版本 1/)).toBeVisible()
  await expect(page.getByText('有未保存的编辑', { exact: true })).not.toBeVisible()
  await page.getByLabel('报告正文').fill('版本1之后的编辑')
  await expect(page.getByText('有未保存的编辑', { exact: true })).toBeVisible()
})

test('allowed without execution is never presented as an executed query', async ({ page }) => {
  await project(page)
  await page.getByRole('link', { name: '查询与审计', exact: true }).click()
  await page.route('**/secure-queries', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    decision: 'allowed', execution_status: 'not_executed', columns: [], rows: [], mock: true,
    reason: { code: 'EXECUTION_PENDING', message: 'PRIVATE_EXECUTION_DETAIL' }, policy_id: 'browser-contract-fixture',
    request_id: '00000000-0000-4000-8000-000000000409',
  }) }))
  await page.getByRole('button', { name: '提交 Mock 查询' }).click()
  await expect(page.getByText('尚未确认执行，策略通过不等于执行成功', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '查询结果' }).getByText('allowed / not_executed', { exact: true })).toBeVisible()
  await expect(page.getByText('PRIVATE_EXECUTION_DETAIL')).not.toBeVisible()
  await expect(page.getByText('查询已完成，当前范围无结果')).not.toBeVisible()
})

test('401 headers still clear protected UI if the response body times out', async ({ page }) => {
  await project(page)
  const result = await page.evaluate(async () => {
    const modulePath = '/src/api/client.ts', { api } = await import(modulePath)
    const originalFetch = window.fetch, originalTimeout = AbortSignal.timeout
    AbortSignal.timeout = () => originalTimeout(25)
    window.fetch = async request => {
      const signal = (request as Request).signal
      return new Response(new ReadableStream({ start(controller) {
        signal.addEventListener('abort', () => controller.error(signal.reason), { once: true })
      } }), { status: 401, headers: { 'X-Request-ID': '00000000-0000-4000-8000-000000000410' } })
    }
    try { await api.health(); return 0 }
    catch (error) { return (error as { status: number }).status }
    finally { window.fetch = originalFetch; AbortSignal.timeout = originalTimeout }
  })
  expect(result).toBe(401)
  await expect(page.getByRole('heading', { name: '开发演示登录' })).toBeVisible()
  await expect(page.getByRole('alert').filter({ hasText: '登录已失效' })).toContainText('00000000-0000-4000-8000-000000000410')
  await expect(page.getByRole('heading', { name: '文献工作台' })).not.toBeVisible()
})
