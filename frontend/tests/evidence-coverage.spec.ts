import { test, expect } from '@playwright/test'

test('evidence results disclose that source coverage may be incomplete', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  await page.getByLabel('新项目名称').fill(`证据覆盖-${Date.now()}`)
  await page.getByRole('button', { name: '创建项目' }).click()
  await page.getByLabel('选择 PDF 文件').setInputFiles({ name: 'source.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nEvidence fixture') })
  await page.getByLabel('论文标题').fill('证据论文')
  await page.getByRole('button', { name: '上传并模拟处理' }).click()
  await expect(page.locator('.document-table .el-table__body tr').filter({ hasText: '证据论文' })).toContainText('可检索')
  await page.getByRole('link', { name: '研究', exact: true }).click()
  await page.getByRole('button', { name: '检索', exact: true }).click()
  await page.getByRole('button', { name: '提取 Mock 证据' }).first().click()
  await expect(page.getByText('结果基于有限来源片段，可能未覆盖全文；请结合原文核验。')).toBeVisible()
})
