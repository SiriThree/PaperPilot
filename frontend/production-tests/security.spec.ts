import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'

test('trusted HTTPS production login, native SQL and matching deployment acceptance are visible', async ({ page }, testInfo) => {
  const accounts = JSON.parse(readFileSync('../.runtime/production/acceptance-secrets.json', 'utf8'))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible()
  await page.getByLabel('账号', { exact: true }).fill(accounts.owner.username)
  await page.getByLabel('密码', { exact: true }).fill(accounts.owner.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
  await page.locator('.project-card').filter({ hasText: '安全验收样例 A（非研究数据）' }).getByRole('button', { name: '进入工作台' }).click()
  await page.getByRole('link', { name: '查询与审计', exact: true }).click()
  const deployment = page.getByRole('region', { name: '当前生产安全配置' })
  await expect(deployment).toContainText('当前部署安全验收通过')
  await expect(deployment).toContainText('文献处理 AI 尚未启用')
  const modelMode = (await page.evaluate(async () => (await fetch('/api/v1/health')).json())).deployment.statistics_sql_mode === 'model'
  if (modelMode) await expect(deployment).toContainText('统计查询模型：')
  await deployment.getByRole('button', { name: '重新检查当前服务', exact: true }).click()
  await expect(deployment).toContainText('预检通过')
  await page.getByLabel('统计问题').fill(modelMode ? '每年分别有多少篇论文？' : '按年份统计论文数量')
  const queryResponse = page.waitForResponse(response => response.url().endsWith('/secure-queries') && response.request().method() === 'POST', { timeout: 100000 })
  await page.getByRole('button', { name: '提交查询', exact: true }).click()
  const query = await (await queryResponse).json()
  expect(query.policy_id).toBe(`postgres-statistics-v2-${modelMode ? 'model' : 'template'}`)
  expect(query.columns).toEqual(['year', 'count'])
  const result = page.getByRole('region', { name: '查询结果' })
  await expect(result).toContainText('allowed / executed', { timeout: 100000 })
  await expect(result).toContainText('本次真实查询结果')
  if (modelMode) {
    await expect(page.getByText('当前由模型理解问题并生成候选 SQL', { exact: false })).toBeVisible()
    await expect(page.getByText('适配器返回结果，待验收', { exact: true })).toHaveCount(0)
  }
  await expect(page.getByRole('heading', { name: '历史回归测试证据', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '攻防实验演示' }).getByRole('button', { name: '开始实时实验', exact: true })).toBeDisabled()
  await deployment.evaluate(el => el.scrollIntoView({ block: 'start' }))
  await page.screenshot({ path: testInfo.outputPath('production-security.png') })
})

test.afterEach(async ({ page }) => {
  if (!page.url().startsWith(process.env.PAPERPILOT_PRODUCTION_URL!)) return
  await page.evaluate(async () => {
    const token = document.cookie.split('; ').find(value => value.startsWith('pp_csrf='))?.slice('pp_csrf='.length)
    if (token) await fetch('/api/v1/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': decodeURIComponent(token) } })
  })
})
