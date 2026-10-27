import { test, expect, type Page } from '@playwright/test'

const projectName = '科研证据追溯演示'
async function enter(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  await page.locator('.project-card').filter({ has: page.getByRole('heading', { name: projectName, exact: true }) }).getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
}
async function screenshot(page: Page, name: string) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `../output/demo-data/${name}.png`, fullPage: true, animations: 'disabled' })
}

for (const width of [390, 1440]) {
  test(`seeded demo at ${width}px shows literature, evidence, snapshots and actual Mock audit`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await enter(page)
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    const table = page.locator('.document-table')
    await expect(table.locator('.el-table__body tbody tr')).toHaveCount(6)
    await expect(page.getByText('处理失败', { exact: true })).toBeVisible()
    await screenshot(page, `${width}-papers`)
    await page.getByLabel('起始年份', { exact: true }).fill('2023')
    await page.getByLabel('结束年份', { exact: true }).fill('2024')
    await page.getByRole('button', { name: '筛选', exact: true }).click()
    await expect(table.locator('.el-table__body tbody tr')).toHaveCount(2)
    await page.getByRole('button', { name: '详情', exact: true }).first().click()
    await page.getByText('[1] 第 3 页 · 合成来源，待核验', { exact: true }).click()
    await expect(page.locator('.source-excerpt p').filter({ hasText: '引用标记关联到论文片段' })).toBeVisible()
    await screenshot(page, `${width}-source`)
    await page.getByRole('button', { name: 'Close this dialog' }).click()
    await page.getByRole('link', { name: '研究', exact: true }).click()
    let evidenceCreates = 0
    page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/evidence')) evidenceCreates++ })
    await page.getByRole('button', { name: '检索', exact: true }).click()
    await expect(page.getByText('10 条结果', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '查看已有证据', exact: true }).first().click()
    await expect(page.getByText('已读取该文献最近保存的证据，未重新提取。')).toBeVisible()
    await page.locator('.evidence-fields').getByRole('button', { name: /实验结果/ }).click()
    await expect(page.getByRole('region', { name: '实验结果证据不足', exact: true }).getByText('未提及或证据不足')).toBeVisible()
    expect(evidenceCreates).toBe(0)
    await page.getByRole('button', { name: '科研证据追溯综述（合成演示） · v2', exact: true }).click()
    await expect(page.getByLabel('报告正文')).toHaveValue(/第二版补充：证据缺口/)
    await page.getByRole('button', { name: '版本 1', exact: true }).click()
    await expect(page.getByLabel('报告正文')).not.toHaveValue(/第二版补充：证据缺口/)
    await page.getByRole('button', { name: '引用清单', exact: true }).click()
    await expect(page.locator('.source-guide')).toContainText('2 条来源')
    await page.getByRole('button', { name: 'Close this dialog' }).click()
    await page.getByRole('button', { name: '版本 2', exact: true }).click()
    await page.getByRole('button', { name: '引用清单', exact: true }).click()
    await expect(page.locator('.source-guide')).toContainText('3 条来源')
    await page.getByRole('button', { name: 'Close this dialog' }).click()
    await screenshot(page, `${width}-research`)
    await page.getByRole('link', { name: '查询与审计', exact: true }).click()
    await expect(page.locator('.audit-table')).toContainText('secure_query.finished')
    await page.getByRole('button', { name: '提交 Mock 查询', exact: true }).click()
    await expect(page.getByText('演示结果已返回，未执行真实 SQL', { exact: true })).toBeVisible()
    const values = await page.locator('.query-result-table .el-table__body tbody tr td:last-child').allTextContents()
    expect(values.reduce((sum, text) => sum + Number(text.trim()), 0)).toBe(6)
    await screenshot(page, `${width}-security`)
    expect(errors).toEqual([])
  })
}

test('existing evidence empty and service failure remain read-only and recover without a new task', async ({ page }) => {
  await enter(page)
  await page.getByRole('link', { name: '研究', exact: true }).click()
  await page.getByRole('button', { name: '检索', exact: true }).click()
  let writes = 0, reads = 0
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/evidence')) writes++ })
  const secondPaper = page.locator('.source-hit').nth(2)
  await secondPaper.getByRole('button', { name: '查看已有证据' }).click()
  await expect(page.getByText('当前文献尚未保存结构化证据，可使用“提取证据”创建任务。')).toBeVisible()
  await page.route('**/papers/*/evidence', route => {
    if (route.request().method() !== 'GET') return route.continue()
    reads++
    if (reads === 1) return route.fulfill({ status: 503, contentType: 'application/json', json: { code: 'DATA_UNAVAILABLE', message: 'PRIVATE_PROVIDER_MESSAGE', request_id: '00000000-0000-4000-8000-000000000001' } })
    return route.continue()
  })
  await page.getByRole('button', { name: '查看已有证据' }).first().click()
  await expect(page.getByRole('alert')).toContainText('00000000-0000-4000-8000-000000000001')
  await expect(page.getByText('PRIVATE_PROVIDER_MESSAGE')).not.toBeVisible()
  await page.getByRole('button', { name: '查看已有证据' }).first().click()
  await expect(page.getByText('已读取该文献最近保存的证据，未重新提取。')).toBeVisible()
  expect(reads).toBe(2)
  expect(writes).toBe(0)
})
