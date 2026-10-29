import { test, expect, type Page } from '@playwright/test'

async function project(page: Page, papers = false) {
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  await page.getByLabel('新项目名称').fill(`联动验收-${Date.now()}`)
  await page.getByRole('button', { name: '创建项目' }).click()
  await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
  const id = new URL(page.url()).pathname.split('/')[2]!
  if (papers) for (const number of [1, 2]) {
    await page.getByLabel('选择 PDF 文件').setInputFiles({ name: `source-${number}.pdf`, mimeType: 'application/pdf', buffer: Buffer.from(`%PDF-1.4\nLinked fixture ${number}`) })
    await page.getByLabel('论文标题').fill(`联动文献 ${number}`)
    await page.getByRole('button', { name: '上传并模拟处理' }).click()
    const row = page.locator('.document-table .el-table__body tr').filter({ has: page.getByText(`联动文献 ${number}`, { exact: true }) })
    await expect(row).toContainText('可检索')
    await expect(page.getByRole('region', { name: '任务恢复' })).toContainText('任务：succeeded')
  }
  return id
}

test('paper selection, answer citation and report snapshots share the same source', async ({ page }) => {
  const id = await project(page, true)
  const papers = (await (await page.request.get(`/api/v1/projects/${id}/papers`)).json()).items
  await page.getByRole('link', { name: '研究', exact: true }).click()
  await page.getByRole('button', { name: '选择文献 联动文献 2', exact: true }).click()
  const second = (await (await page.request.get(`/api/v1/projects/${id}/papers/${papers[1].paper_id}`)).json()).sources[0]
  await expect(page.getByTestId('active-source')).toHaveAttribute('data-active-chunk', second.chunk_id)
  await page.getByRole('button', { name: '添加来源到报告' }).click()
  await expect(page.getByLabel('报告正文')).toHaveValue(/\[1\]/)
  await page.getByRole('button', { name: '添加来源到报告' }).click()
  await expect(page.getByText('该来源已在引用清单 [1] 中，未重复添加。')).toBeVisible()
  await page.getByRole('button', { name: '保存新版本' }).click()
  await expect(page.getByRole('button', { name: '版本 1', exact: true })).toBeVisible()
  const saved = (await (await page.request.get(`/api/v1/projects/${id}/reports`)).json()).items[0]
  expect(saved.sources).toHaveLength(1)
  expect(saved.sources[0].chunk_id).toBe(second.chunk_id)
  expect(saved.sources[0].synthetic).toBe(true)
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await page.getByRole('button', { name: '查看引用 [1]' }).click()
  await expect(page.getByRole('dialog', { name: '引用来源 · 合成数据' })).toBeVisible()
  await page.getByRole('button', { name: 'Close this dialog' }).click()
  const first = (await (await page.request.get(`/api/v1/projects/${id}/papers/${papers[0].paper_id}`)).json()).sources[0]
  await expect(page.getByTestId('active-source')).toHaveAttribute('data-active-chunk', first.chunk_id)
  await expect(page.getByRole('button', { name: '选择文献 联动文献 1', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: '查看引用 [1]' })).toHaveAttribute('aria-pressed', 'true')
  await page.evaluate(() => window.scrollTo(0, document.querySelector('.research-flow')!.getBoundingClientRect().top + window.scrollY - 24))
  await page.screenshot({ path: '../output/connected-workspace/desktop-linked-reading.png', animations: 'disabled' })
})

test('late source response cannot replace a later selected document', async ({ page }) => {
  const id = await project(page, true)
  const papers = (await (await page.request.get(`/api/v1/projects/${id}/papers`)).json()).items
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const lateResponse = page.waitForResponse(response => response.url().endsWith(`/papers/${papers[0].paper_id}`) && response.request().method() === 'GET')
  await page.route(`**/papers/${papers[0].paper_id}`, async route => { await gate; await route.continue() })
  await page.getByRole('link', { name: '研究', exact: true }).click()
  await page.getByRole('button', { name: '选择文献 联动文献 2', exact: true }).click()
  await expect(page.getByRole('region', { name: '当前来源阅读区' })).toContainText('联动文献 2')
  release()
  await (await lateResponse).finished()
  // Wait for browser rendering after the released body; an immediate negative assertion
  // could otherwise succeed before the stale response has actually been consumed.
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  await expect(page.getByRole('region', { name: '当前来源阅读区' })).not.toContainText('联动文献 1')
})

test('project switch protects unsaved edits, then clears old project data after explicit discard', async ({ page }) => {
  const id = await project(page, true)
  await page.getByRole('link', { name: '研究', exact: true }).click()
  await page.getByLabel('报告正文').fill('不能静默丢失的编辑')
  const switcher = page.getByRole('combobox', { name: '切换项目' })
  await switcher.press('ArrowDown')
  await page.getByRole('option', { name: '文献研究演示项目', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '离开前处理未保存编辑' })
  await expect(dialog).toBeVisible()
  await page.getByRole('button', { name: '留在当前项目' }).click()
  await expect(page).toHaveURL(new RegExp(id))
  await expect(page.getByLabel('报告正文')).toHaveValue('不能静默丢失的编辑')
  await switcher.press('ArrowDown')
  await page.getByRole('option', { name: '文献研究演示项目', exact: true }).click()
  await page.getByRole('button', { name: '放弃未保存编辑并继续' }).click()
  await expect(page).not.toHaveURL(new RegExp(id))
  await expect(page.getByLabel('报告正文')).toHaveValue('')
  await expect(page.getByRole('region', { name: '当前来源阅读区' })).not.toContainText('联动文献')
})

test('failed draft retains manual edits and the acknowledged saved version', async ({ page }) => {
  const id = await project(page)
  await page.getByRole('link', { name: '研究', exact: true }).click()
  await page.getByLabel('报告正文').fill('原有正文')
  await page.getByRole('button', { name: '保存新版本' }).click()
  await expect(page.getByRole('button', { name: '版本 1', exact: true })).toBeVisible()
  await page.getByLabel('报告正文').fill('生成失败也不能覆盖的手工编辑')
  // Inject a conforming failed-task response: Mock draft generation itself has no failure trigger.
  await page.route('**/tasks/*', async route => {
    if (route.request().method() !== 'GET') return route.continue()
    const response = await route.fetch(), task = await response.json()
    await route.fulfill({ response, json: { ...task, status: 'failed', result: null, result_ref: null, error: { code: 'AI_UNAVAILABLE', message: 'PRIVATE_PROVIDER_DETAIL' } } })
  })
  await page.getByRole('button', { name: '生成 Mock 草稿' }).click()
  await expect(page.getByText('草稿生成失败，当前编辑和已保存版本未被覆盖。可查询原任务或明确重试。')).toBeVisible()
  await expect(page.getByLabel('报告正文')).toHaveValue('生成失败也不能覆盖的手工编辑')
  await expect(page.getByText('PRIVATE_PROVIDER_DETAIL')).not.toBeVisible()
  const saved = (await (await page.request.get(`/api/v1/projects/${id}/reports`)).json()).items[0]
  expect(saved.body).toBe('原有正文')
  expect(saved.number).toBe(1)
})

test('fresh scope response without membership blocks submission before the gateway', async ({ page }) => {
  const id = await project(page)
  await page.getByRole('link', { name: '查询与审计', exact: true }).click()
  await expect(page.getByTestId('scope-role')).toHaveText('负责人')
  let queries = 0
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/secure-queries')) queries++ })
  await page.route('**/api/v1/projects', async route => {
    if (route.request().method() !== 'GET') return route.continue()
    const response = await route.fetch(), data = await response.json()
    await route.fulfill({ response, json: { ...data, items: data.items.filter((item: { project_id: string }) => item.project_id !== id) } })
  })
  await page.getByRole('button', { name: '提交 Mock 查询' }).click()
  await expect(page.getByTestId('scope-role')).toHaveText('待确认')
  await expect(page.getByRole('alert').filter({ hasText: 'RESOURCE_NOT_FOUND' })).toBeVisible()
  expect(queries).toBe(0)
  await expect(page.locator('.query-result-table')).toHaveCount(0)
})

test('a missing cited chunk never highlights a different paragraph as its source', async ({ page }) => {
  await project(page, true)
  await page.getByRole('link', { name: '研究', exact: true }).click()
  await page.route('**/tasks/*', async route => {
    if (route.request().method() !== 'GET') return route.continue()
    const response = await route.fetch(), task = await response.json()
    if (task.result?.kind === 'answer') task.result.sources[0].chunk_id = '00000000-0000-4000-8000-000000000099'
    await route.fulfill({ response, json: task })
  })
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await page.getByRole('button', { name: '查看引用 [1]' }).click()
  await page.getByRole('button', { name: 'Close this dialog' }).click()
  await expect(page.getByRole('region', { name: '当前来源阅读区' })).toContainText('引用片段已不在当前文献中')
  await expect(page.getByTestId('active-source')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '添加来源到报告' })).toHaveCount(0)
})

test('a reported failed security group keeps its actual failure count and no passed badge', async ({ page }) => {
  await page.route('**/security-evidence.json', async route => {
    const response = await route.fetch(), report = await response.json()
    report.groups[0] = { ...report.groups[0], status: 'failed', passed: 0, failed: 1, skipped: 0 }
    await route.fulfill({ response, json: report })
  })
  await project(page)
  await page.getByRole('link', { name: '查询与审计', exact: true }).click()
  const group = page.locator('.security-checks li').filter({ hasText: '匿名资源访问' })
  await expect(group).toContainText('0 通过 / 1 失败 / 0 跳过')
  await expect(group.locator('.check-failed')).toHaveText('存在失败')
  await expect(group.locator('.check-passed')).toHaveCount(0)
})

for (const failure of ['missing', 'invalid']) {
  test(`security evidence ${failure} never displays fabricated passed results`, async ({ page }) => {
    await page.route('**/security-evidence.json', route => failure === 'missing'
      ? route.fulfill({ status: 404, body: '' })
      : route.fulfill({ status: 200, json: { schema_version: 1, scope: 'real_security', groups: [{ status: 'passed' }] } }))
    await project(page)
    await page.getByRole('link', { name: '查询与审计', exact: true }).click()
    const evidence = page.getByRole('region', { name: '安全验证证据' })
    await expect(evidence).toContainText('未取得有效报告')
    await expect(evidence.locator('.check-passed')).toHaveCount(0)
    await expect(evidence).toContainText('待 D 接入验收')
    await expect(evidence).toContainText('待 A 接入验收')
  })
}

for (const width of [390, 1440]) {
  test(`connected reading and actual verification evidence at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    await project(page, true)
    await page.getByRole('link', { name: '研究', exact: true }).click()
    await expect(page.getByTestId('active-source')).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../output/connected-workspace/${width}-research.png`, fullPage: true })
    await page.getByRole('tab', { name: '报告', exact: true }).focus()
    await page.keyboard.press('Home')
    await expect(page.getByRole('tab', { name: '问答', exact: true })).toHaveAttribute('aria-selected', 'true')
    await page.getByRole('link', { name: '查询与审计', exact: true }).click()
    await expect(page.getByRole('region', { name: '安全验证证据' })).toContainText('应用层测试通过（Mock）')
    await expect(page.getByRole('region', { name: '安全验证证据' })).toContainText('待 D 接入验收')
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../output/connected-workspace/${width}-security.png`, fullPage: true })
  })
}
