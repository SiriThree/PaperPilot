import { test, expect } from '@playwright/test'

for (const mode of ['model', 'template']) {
  test(`statistics source disclosure: ${mode}`, async ({ page }) => {
    // This checks source disclosure only; real model/executor integration is tested in Python.
    await page.route('**/api/v1/health', async route => {
      const response = await route.fetch()
      const health = await response.json()
      health.modes.d = 'real'
      health.limitations.push(`statistics_sql_${mode}`)
      await route.fulfill({ response, json: health })
    })
    await page.goto('/')
    await page.getByRole('button', { name: '进入演示', exact: true }).click()
    await page.locator('.project-card').first().getByRole('button', { name: '进入工作台' }).click()
    await page.getByRole('link', { name: '查询与审计', exact: true }).click()
    const notice = page.locator('p.gateway-notice')
    await expect(notice).toContainText(mode === 'model' ? '由模型理解问题并生成候选 SQL' : '不调用模型生成 SQL')
    await expect(notice).toContainText(mode === 'model' ? '仍只允许三类已审核统计结构' : '三类固定模板降级')
  })
}
