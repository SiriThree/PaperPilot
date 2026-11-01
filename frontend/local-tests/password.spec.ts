import { readFileSync } from 'node:fs'
import { test, expect } from '@playwright/test'

test('personal account, real SQL audit, browser refresh and logout', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '账户登录', exact: true })).toBeVisible()
  await expect(page.getByLabel('演示身份')).toHaveCount(0)
  await page.getByLabel('账户名', { exact: true }).fill('owner')
  await page.getByLabel('密码', { exact: true }).fill(readFileSync(new URL('../../.runtime/local-deployment/secrets/owner-password', import.meta.url), 'utf8').trim())
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
  await expect(page.getByText('账户登录 · 含模拟模块', { exact: true })).toBeVisible()
  await page.locator('.project-card').filter({ hasText: '本机持久项目' }).getByRole('button', { name: '进入工作台' }).click()
  await page.getByRole('link', { name: '查询与审计', exact: true }).first().click()
  await page.getByRole('button', { name: '提交查询', exact: true }).click()
  await expect(page.getByText('网关报告已执行，实际安全能力待验收', { exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'security.query.finished', exact: true }).first()).toBeVisible()
  await page.reload()
  await expect(page.getByTestId('scope-role')).toHaveText('负责人')
  await page.getByRole('button', { name: /退出/ }).first().click()
  await expect(page.getByRole('heading', { name: '账户登录', exact: true })).toBeVisible()
  expect(errors).toEqual([])
})
