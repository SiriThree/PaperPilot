import { test, expect } from '@playwright/test'

for (const width of [390, 1440]) {
test(`audit details at ${width}px retain submitted text, escape names and reset filters`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width, height: 1000 })
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  await page.getByLabel('新项目名称').fill(`审计详情-${Date.now()}`)
  await page.getByRole('button', { name: '创建项目', exact: true }).click()
  await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
  const project = new URL(page.url()).pathname.split('/')[2]!
  const base = `/api/v1/projects/${project}`
  const headers = { 'X-PaperPilot-Client': 'web' }
  const title = '<img src=x onerror="window.__auditXss=1"> 论文标题'
  const upload = await page.request.post(`${base}/papers`, { headers: { ...headers, 'Idempotency-Key': `audit-${Date.now()}` }, multipart: { title, file: { name: 'audit.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 audit detail fixture') } } })
  expect(upload.status()).toBe(202)
  const paper = (await upload.json()).paper
  expect((await page.request.delete(`${base}/papers/${paper.paper_id}?confirm_source_impact=true`, { headers })).ok()).toBeTruthy()
  await page.getByRole('link', { name: '查询与审计', exact: true }).click()
  await page.getByRole('button', { name: '提交 Mock 查询', exact: true }).click()
  await page.getByLabel('统计问题').fill('修改后尚未提交的问题')
  await page.getByLabel('审计事件类型').fill('secure_query.finished')
  await page.getByRole('button', { name: '筛选审计', exact: true }).click()
  await page.getByRole('button', { name: '查看详情', exact: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '审计操作详情' })
  await expect(dialog.getByRole('heading', { name: '提交的问题／查询内容' })).toBeVisible()
  await expect(dialog).toContainText('按年份统计论文数量')
  await expect(dialog).not.toContainText('修改后尚未提交的问题')
  await dialog.getByRole('button', { name: 'Close this dialog' }).click()
  await page.getByLabel('审计事件类型').fill('paper.removed')
  await page.getByRole('button', { name: '筛选审计', exact: true }).click()
  await page.getByRole('button', { name: '查看详情', exact: true }).first().click()
  await expect(dialog).toContainText(title)
  await expect(dialog).toContainText(paper.paper_id)
  await expect(dialog.locator('img')).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('audit-operation-details.png'), animations: 'disabled' })
  expect(await page.evaluate(() => '__auditXss' in window)).toBe(false)
  await dialog.getByRole('button', { name: 'Close this dialog' }).click()
  await page.getByRole('button', { name: '清空筛选', exact: true }).click()
  await expect(page.getByLabel('审计事件类型')).toHaveValue('')
  await expect(page.locator('.audit-table')).toContainText('secure_query.finished')
})
}
