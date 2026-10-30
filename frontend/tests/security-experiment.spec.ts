import { test, expect, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'

const fixture = () => JSON.parse(readFileSync('public/security-experiment.json', 'utf8'))
async function open(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
  await page.locator('.project-card').first().getByRole('button', { name: '进入工作台' }).click()
  await page.getByRole('link', { name: '查询与审计', exact: true }).click()
  return page.getByRole('region', { name: '攻防实验历史回放' })
}

for (const width of [390, 1440]) {
  test(`actual three-way experiment can be demonstrated at ${width}px without posting SQL`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 })
    const panel = await open(page)
    let queries = 0
    page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/secure-queries')) queries++ })
    await expect(panel).toContainText('完整配置：本次固定实验达到目标')
    await expect(panel.getByTestId('experiment-no_gateway')).toContainText('越界候选已执行')
    await expect(panel.getByTestId('experiment-no_gateway')).toContainText('1 行')
    await expect(panel.getByTestId('experiment-gateway_and_database')).toContainText('策略拒绝，未执行')
    await panel.getByRole('button', { name: '演示危险写入', exact: true }).click()
    await expect(panel.getByLabel('固定候选 SQL 模板')).toContainText('UPDATE public.papers')
    await panel.getByRole('button', { name: '演示论文提示注入', exact: true }).click()
    await expect(panel).toContainText('不可信论文内容')
    await panel.getByTestId('experiment-gateway_and_database').getByText('查看本例原始结果与审计', { exact: true }).click()
    await expect(panel.getByTestId('experiment-gateway_and_database')).toContainText('security.query.finished')
    await panel.getByRole('button', { name: '演示正常查询', exact: true }).click()
    for (const id of ['no_gateway', 'gateway_only', 'gateway_and_database']) await expect(panel.getByTestId(`experiment-${id}`)).toContainText('正常结果一致')
    await panel.getByText('为什么还需要数据库权限？查看绕过网关的实际探测', { exact: true }).click()
    await expect(panel.locator('.experiment-probes')).toContainText('数据库原生权限拒绝')
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('security-experiment.png'), fullPage: true, animations: 'disabled' })
    expect(queries).toBe(0)
  })
}

for (const mutation of ['missing', 'wrong-scope', 'missing-case', 'forged-rate', 'forged-p95', 'missing-audit', 'forged-accepted']) {
  test(`incomplete or inconsistent experiment ${mutation} never displays acceptance`, async ({ page }) => {
    await page.route('**/security-experiment.json', async route => {
      if (mutation === 'missing') { await route.fulfill({ status: 404 }); return }
      const report = fixture(), full = report.configurations[2]
      if (mutation === 'wrong-scope') report.scope = 'mock_demo'
      if (mutation === 'missing-case') full.records.pop()
      if (mutation === 'forged-rate') full.attack_success_rate = .5
      if (mutation === 'forged-p95') full.performance[0].gateway_added_p95_ms = 0
      if (mutation === 'missing-audit') full.records[0].audit = []
      if (mutation === 'forged-accepted') report.accepted = false
      await route.fulfill({ json: report })
    })
    const panel = await open(page)
    await expect(panel).toContainText('实验报告不可用或记录不完整')
    await expect(panel.locator('.experiment-verdict')).toHaveCount(0)
  })
}

test('a genuine measured target miss is shown as failure with its samples', async ({ page }) => {
  await page.route('**/security-experiment.json', async route => {
    const report = fixture(), performance = report.configurations[2].performance[0]
    for (const sample of performance.samples) { sample.total_ms += 300; sample.gateway_added_ms += 300 }
    const p95 = (key: string) => performance.samples.map((s: Record<string, number>) => s[key]).sort((a: number, b: number) => a - b)[Math.ceil(performance.samples.length * .95) - 1]
    performance.gateway_added_p95_ms = p95('gateway_added_ms')
    performance.total_p95_ms = p95('total_ms')
    report.accepted = false
    await route.fulfill({ json: report })
  })
  const panel = await open(page)
  await expect(panel.getByRole('status')).toContainText('本次实验未达到全部验收目标')
  await expect(panel.locator('.experiment-verdict')).toHaveClass(/experiment-fail/)
  await panel.getByText('测量口径、来源与现场复跑', { exact: true }).click()
  await expect(panel).toContainText('目标 ≤200 ms')
})
