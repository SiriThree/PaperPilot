import { test, expect } from '@playwright/test'

for (const width of [390, 768, 1024, 1440]) {
  test(`workspace pages remain usable at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto('/')
    await expect(page.getByRole('heading', { name: '开发演示登录' })).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('login.png'), animations: 'disabled' })
    await page.getByRole('button', { name: '进入演示' }).click()
    await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('projects.png'), animations: 'disabled' })
    await page.getByLabel('新项目名称').fill(`工作台布局验证 ${width}`)
    await page.getByRole('button', { name: '创建项目' }).click()
    await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
    const projectPath = new URL(page.url()).pathname.replace(/\/papers$/, '')
    for (const [path, title] of [['papers', '文献工作台'], ['research', '研究工作台'], ['security', '查询与审计']] as const) {
      await page.goto(`${projectPath}/${path}`)
      await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.screenshot({ path: testInfo.outputPath(`${path}.png`), animations: 'disabled' })
    }
    expect(errors).toEqual([])
  })
}
