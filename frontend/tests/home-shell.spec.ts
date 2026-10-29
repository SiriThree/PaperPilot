import { test, expect, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '开发演示登录' })).toBeVisible()
  await page.getByRole('button', { name: '进入演示' }).click()
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
}

for (const width of [304, 390, 768, 1440]) {
  test(`homepage and frame at ${width}px keep honest identity and real project counts`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto('/')
    await expect(page.getByRole('heading', { name: '开发演示登录' })).toBeVisible()
    await expect(page.getByText('仅切换演示身份，不验证真实密码。', { exact: false })).toBeVisible()
    await expect(page.getByText('演示数据', { exact: true })).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../output/home-redesign/preview-${width}-login.png`, fullPage: true, animations: 'disabled' })
    await page.getByRole('button', { name: '进入演示' }).click()
    await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
    const response = await page.request.get('/api/v1/projects')
    expect(response.ok()).toBe(true)
    const { items } = await response.json()
    await expect(page.getByTestId('project-count')).toHaveText(`${items.length}个项目`)
    await expect(page.locator('.project-card')).toHaveCount(items.length)
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `../output/home-redesign/preview-${width}-projects.png`, fullPage: true, animations: 'disabled' })
    expect(errors).toEqual([])
  })
}

test('desktop navigation and project switch preserve route context and original business pages', async ({ page }) => {
  await login(page)
  await page.getByLabel('新项目名称').fill(`框架导航-${Date.now()}`)
  await page.getByRole('button', { name: '创建项目' }).click()
  await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
  await page.getByRole('link', { name: '研究', exact: true }).click()
  await expect(page.getByRole('heading', { name: '研究工作台' })).toBeVisible()
  await page.getByRole('combobox', { name: '切换项目' }).press('ArrowDown')
  await page.getByRole('option', { name: '文献研究演示项目', exact: true }).click()
  await expect(page.getByRole('heading', { name: '研究工作台' })).toBeVisible()
  await expect(page).toHaveURL(/\/research$/)
  await page.getByRole('link', { name: '查询与审计', exact: true }).click()
  await expect(page.getByRole('heading', { name: '查询与审计' })).toBeVisible()
  await page.getByRole('link', { name: '← 全部项目', exact: true }).click()
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
})

test('mobile menu opens and closes while direct project navigation remains usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page)
  await page.locator('.project-card').first().getByRole('button', { name: '进入工作台' }).click()
  await page.getByRole('button', { name: '打开导航菜单' }).click()
  const drawer = page.locator('.navigation-drawer')
  await expect(drawer).toBeVisible()
  await drawer.getByRole('link', { name: '研究', exact: true }).click()
  await expect(page.getByRole('heading', { name: '研究工作台' })).toBeVisible()
  await expect(drawer).not.toBeVisible()
  await page.getByRole('link', { name: '文献', exact: true }).click()
  await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('compact mode indicator opens the complete limitations without hiding Mock status', async ({ page }) => {
  await login(page)
  await page.getByRole('button', { name: '查看运行说明', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '当前运行说明' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('不验证真实密码')
  await expect(dialog).toContainText('重启后丢失')
  await expect(dialog).toContainText('SQL 安全能力')
})

test('desktop sidebar can collapse and expand without losing navigation', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await login(page)
  const sidebar = page.getByRole('complementary', { name: '侧边栏' })
  await sidebar.getByRole('button', { name: '收起侧边栏' }).click()
  await expect(sidebar.getByRole('button', { name: '展开侧边栏' })).toHaveAttribute('aria-expanded', 'false')
  await expect(sidebar.getByRole('link', { name: '项目总览' })).toBeVisible()
  await sidebar.getByRole('button', { name: '展开侧边栏' }).click()
  await expect(sidebar.getByRole('button', { name: '收起侧边栏' })).toHaveAttribute('aria-expanded', 'true')
  await expect(sidebar.getByRole('link', { name: '项目总览' })).toBeVisible()
})
