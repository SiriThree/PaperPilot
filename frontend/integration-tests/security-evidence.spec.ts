import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'

for (const mutation of ['missing', 'wrong-scope', 'unverified-source', 'insufficient-attacks', 'failed']) {
  test(`real evidence ${mutation} keeps the verification boundary`, async ({ page }) => {
    await page.route('**/security-evidence-postgres.json', async route => {
      if (mutation === 'missing') { await route.fulfill({ status: 404, body: '' }); return }
      const report = JSON.parse(readFileSync('public/security-evidence-postgres.json', 'utf8'))
      if (mutation === 'wrong-scope') report.scope = 'mock_application'
      if (mutation === 'unverified-source') report.source.verified = false
      if (mutation === 'insufficient-attacks') report.groups[0].passed = 1
      if (mutation === 'failed') {
        report.verification_totals.failed = 1
        report.groups = report.groups.map((g: object) => ({ ...g, status: 'not_verified' }))
        report.groups[0] = { ...report.groups[0], status: 'failed', failed: 1, passed: 0 }
      }
      await route.fulfill({ json: report })
    })
    await page.goto('/')
    await page.getByRole('button', { name: '进入演示' }).click()
    await page.locator('.project-card').filter({ hasText: 'D contract A' }).getByRole('button', { name: '进入工作台' }).click()
    await page.getByRole('link', { name: '查询与审计', exact: true }).click()
    const evidence = page.getByRole('region', { name: '安全验证证据' })
    if (mutation === 'failed') {
      const group = evidence.locator('.security-checks li').first()
      await expect(group).toContainText('0 通过 / 1 失败 / 0 跳过')
      await expect(group.locator('.check-passed')).toHaveCount(0)
      await expect(group.locator('.check-failed')).toHaveText('存在失败')
    } else {
      await expect(evidence).toContainText('未取得有效报告')
      await expect(evidence.locator('.check-passed')).toHaveCount(0)
    }
  })
}
