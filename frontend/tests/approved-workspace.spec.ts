import { test, expect, type Page } from '@playwright/test'

async function setup(page: Page, count = 2) {
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  await page.getByLabel('新项目名称').fill(`设计验收-${Date.now()}`)
  await page.getByRole('button', { name: '创建项目' }).click()
  await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
  const id = new URL(page.url()).pathname.split('/')[2]!
  const titles = ['Evidence Grounding in RAG', 'Retrieval-Augmented Generation', 'Evaluating Citation Quality', 'Scientific Claim Verification', 'Long-context Evidence Synthesis', 'Research Notes and Provenance']
  for (let i = 0; i < count; i++) {
    const result = await page.request.post(`/api/v1/projects/${id}/papers`, {
      headers: { 'X-PaperPilot-Client': 'web', 'Idempotency-Key': crypto.randomUUID() },
      multipart: { title: titles[i], file: { name: `paper-${i}.pdf`, mimeType: 'application/pdf', buffer: Buffer.from(`%PDF-1.4\nDesign fixture ${i}`) } },
    })
    expect(result.status()).toBe(202)
    const { task } = await result.json()
    await expect.poll(async () => (await (await page.request.get(`/api/v1/projects/${id}/tasks/${task.task_id}`)).json()).status).toBe('succeeded')
  }
  await page.goto(`/projects/${id}/research`)
  if (count) await expect(page.getByTestId('active-source')).toBeVisible()
  const papers = (await (await page.request.get(`/api/v1/projects/${id}/papers`)).json()).items
  return { id, papers }
}

test('approved layout links citations directly and preserves dirty report versions', async ({ page }, testInfo) => {
  const { papers } = await setup(page, 6)
  await expect(page.locator('.desk-library')).toBeVisible()
  await expect(page.locator('.research-flow')).toHaveCount(0)
  await expect(page.locator('.workspace-mode-note')).toHaveCount(0)
  await page.getByRole('button', { name: /生成材料：已选/ }).click()
  const scope = page.getByRole('dialog', { name: '选择生成材料' })
  await scope.getByRole('button', { name: '清空', exact: true }).click()
  await scope.getByText(papers[1].title, { exact: true }).click()
  await expect(scope.getByRole('checkbox', { name: papers[1].title, exact: true })).toBeChecked()
  await scope.getByRole('button', { name: '完成' }).click()
  const sent = page.waitForRequest(request => request.url().endsWith('/answers') && request.method() === 'POST')
  await page.getByLabel('研究问题', { exact: true }).fill('如何让结论保持可追溯？')
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  expect((await sent).postDataJSON().paper_ids).toEqual([papers[1].paper_id])
  const citation = page.getByRole('button', { name: '查看引用 [1]', exact: true })
  await expect(citation).toBeVisible()
  await citation.click()
  await expect(page.getByRole('dialog', { name: /引用来源/ })).toHaveCount(0)
  const detail = await (await page.request.get(`/api/v1/projects/${papers[1].project_id}/papers/${papers[1].paper_id}`)).json()
  await expect(page.getByTestId('active-source')).toHaveAttribute('data-active-chunk', detail.sources[0].chunk_id)
  await expect(citation).toHaveAttribute('aria-pressed', 'true')
  await page.screenshot({ path: testInfo.outputPath('research-desktop.png'), animations: 'disabled' })
  await page.getByRole('button', { name: '添加来源到报告' }).click()
  await expect(page.getByLabel('报告正文')).toHaveValue(/\[1\]/)
  await page.getByRole('button', { name: '保存新版本' }).click()
  await expect(page.getByRole('button', { name: '版本 1', exact: true })).toBeVisible()
  await page.getByLabel('报告正文').fill('必须保留的手工修改')
  await page.getByRole('button', { name: '版本 1', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '离开前处理未保存编辑' })).toBeVisible()
  await page.getByRole('button', { name: '留在当前项目' }).click()
  await expect(page.getByLabel('报告正文')).toHaveValue('必须保留的手工修改')
  await page.getByRole('button', { name: '版本 1', exact: true }).click()
  await page.getByRole('button', { name: '放弃未保存编辑并继续' }).click()
  await expect(page.getByLabel('报告正文')).not.toHaveValue('必须保留的手工修改')
  const baseline = await page.getByLabel('报告研究问题').inputValue()
  await page.getByRole('tab', { name: '问答', exact: true }).click()
  await page.getByLabel('研究问题', { exact: true }).fill('另一个检索问题')
  await page.getByRole('tab', { name: '报告', exact: true }).click()
  await expect(page.getByLabel('报告研究问题')).toHaveValue(baseline)
})

for (const width of [390, 768, 1024, 1440]) {
  test(`workspace at ${width}px keeps reading and composing inside viewport`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await setup(page)
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await expect(page.getByRole('button', { name: '添加来源到报告' })).toBeInViewport()
    if (width <= 900) await page.getByRole('navigation', { name: '研究视图' }).getByRole('button', { name: '问答' }).click()
    await expect(page.getByLabel('研究问题', { exact: true })).toBeInViewport()
    await page.getByRole('button', { name: '生成 Mock 回答' }).click()
    await page.getByRole('button', { name: '查看引用 [1]', exact: true }).click()
    await expect(page.getByTestId('active-source')).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`research-${width}.png`), animations: 'disabled' })
    expect(errors).toEqual([])
  })
}

test('import is on demand; title search, empty results and document reading work', async ({ page }, testInfo) => {
  const { id } = await setup(page, 0)
  await page.goto(`/projects/${id}/papers`)
  await expect(page.getByLabel('选择 PDF 文件')).toHaveCount(0)
  await page.getByRole('button', { name: '导入文献', exact: true }).click()
  await page.getByLabel('选择 PDF 文件').setInputFiles({ name: 'evidence.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nImport fixture') })
  await page.getByLabel('论文标题').fill('Evidence Grounding')
  await page.getByRole('button', { name: '上传并模拟处理' }).click()
  await expect(page.getByRole('dialog', { name: '导入文献' })).not.toBeVisible()
  await expect(page.locator('.document-table')).toContainText('可检索')
  await page.getByLabel('搜索文献标题').fill('no-match')
  await expect(page.getByText('当前范围内没有文献')).toBeVisible()
  await page.getByRole('button', { name: '清空筛选', exact: true }).click()
  await page.screenshot({ path: testInfo.outputPath('library-desktop.png'), animations: 'disabled' })
  await page.getByRole('link', { name: '阅读', exact: true }).click()
  await expect(page.getByTestId('active-source')).toBeVisible()
})

test('empty generation scope cannot silently expand to the whole project', async ({ page }) => {
  await setup(page)
  await page.getByRole('button', { name: /生成材料：已选/ }).click()
  const scope = page.getByRole('dialog', { name: '选择生成材料' })
  await scope.getByRole('button', { name: '清空', exact: true }).click()
  await scope.getByRole('button', { name: '完成' }).click()
  await expect(page.getByRole('button', { name: '生成 Mock 回答' })).toBeDisabled()
})

test('unavailable citations never highlight a different source', async ({ page }) => {
  await setup(page)
  await page.route('**/tasks/*', async route => {
    const response = await route.fetch(), task = await response.json()
    if (task.result?.kind === 'answer') task.result.sources[0].chunk_id = '00000000-0000-4000-8000-000000000099'
    await route.fulfill({ response, json: task })
  })
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await page.getByRole('button', { name: '查看引用 [1]' }).click()
  await expect(page.getByText('引用片段已不在当前文献中', { exact: true })).toBeVisible()
  await expect(page.getByTestId('active-source')).toHaveCount(0)
})

test('unknown submissions recover the same request after reload without storing private input', async ({ page }) => {
  await setup(page)
  const question = 'PRIVATE_原请求核对问题'
  await page.getByLabel('研究问题', { exact: true }).fill(question)
  const keys: string[] = []
  await page.route('**/answers', async route => {
    keys.push(route.request().headers()['idempotency-key'] || '')
    const response = await route.fetch()
    if (keys.length === 1) await route.abort('failed')
    else await route.fulfill({ response })
  })
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await expect(page.getByRole('region', { name: '任务恢复' })).toContainText('提交结果未知')
  await page.reload()
  await expect(page.getByRole('button', { name: '生成 Mock 回答' })).toBeDisabled()
  expect(keys).toHaveLength(1)
  expect(await page.evaluate(() => Object.keys(sessionStorage).map(key => sessionStorage.getItem(key)).join(''))).not.toContain(question)
  await page.getByRole('button', { name: '使用原请求核对' }).click()
  await expect(page.getByRole('region', { name: '任务恢复' })).toContainText('当前输入与原请求不一致')
  expect(keys).toHaveLength(1)
  await page.getByLabel('研究问题', { exact: true }).fill(question)
  await page.getByRole('button', { name: '使用原请求核对' }).click()
  await expect(page.getByRole('button', { name: '查看引用 [1]' })).toBeVisible()
  expect(keys).toHaveLength(2)
  expect(keys[1]).toBe(keys[0])
})

test('late generated drafts retain manual edits until explicitly replaced', async ({ page }) => {
  await setup(page)
  await page.getByRole('tab', { name: '报告', exact: true }).click()
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/tasks/*', async route => { await gate; await route.continue() })
  await page.getByRole('button', { name: '生成 Mock 草稿' }).click()
  await page.getByLabel('报告正文').fill('任务完成也不能覆盖的内容')
  release()
  await expect(page.getByText('新草稿已完成，当前编辑已保留。')).toBeVisible()
  await expect(page.getByLabel('报告正文')).toHaveValue('任务完成也不能覆盖的内容')
  await page.getByRole('button', { name: '载入生成草稿' }).click()
  await page.getByRole('button', { name: '留在当前项目' }).click()
  await expect(page.getByLabel('报告正文')).toHaveValue('任务完成也不能覆盖的内容')
})

test('project navigation protects unsaved reports and isolates the next project', async ({ page }) => {
  const { id } = await setup(page)
  await page.getByRole('tab', { name: '报告', exact: true }).click()
  await page.getByLabel('报告正文').fill('当前项目的编辑')
  const selector = page.getByRole('combobox', { name: '切换项目' })
  await selector.press('ArrowDown')
  await page.getByRole('option', { name: '文献研究演示项目', exact: true }).click()
  await page.getByRole('button', { name: '留在当前项目' }).click()
  await expect(page).toHaveURL(new RegExp(id))
  await expect(page.getByLabel('报告正文')).toHaveValue('当前项目的编辑')
  await selector.press('ArrowDown')
  await page.getByRole('option', { name: '文献研究演示项目', exact: true }).click()
  await page.getByRole('button', { name: '放弃未保存编辑并继续' }).click()
  await expect(page).not.toHaveURL(new RegExp(id))
  await page.getByRole('tab', { name: '报告', exact: true }).click()
  await expect(page.getByLabel('报告正文')).toHaveValue('')
})
