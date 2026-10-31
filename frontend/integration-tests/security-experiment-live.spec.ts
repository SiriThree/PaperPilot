import { test, expect } from '@playwright/test'
import { writeFileSync } from 'node:fs'

test('browser starts a fresh Docker experiment and displays that exact measured run', async ({ page }, testInfo) => {
  test.skip(process.env.PAPERPILOT_LIVE_EXPERIMENT !== '1', 'Explicit opt-in for the complete disposable Docker experiment')
  test.setTimeout(900000)
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  await page.locator('.project-card').filter({ hasText: 'D contract A' }).getByRole('button', { name: '进入工作台' }).click()
  await page.getByRole('link', { name: '查询与审计', exact: true }).click()
  const panel = page.getByRole('region', { name: '攻防实验演示' })
  const finished = page.waitForResponse(async r => {
    if (r.request().method() !== 'GET' || !r.url().endsWith('/security-experiments')) return false
    const data = await r.json()
    return !data.busy && ['succeeded', 'failed'].includes(data.status)
  }, { timeout: 880000 })
  const accepted = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/security-experiments'))
  await panel.getByRole('button', { name: '开始实时实验', exact: true }).click()
  const start = await accepted, task = await start.json()
  expect(start.status()).toBe(202)
  expect(task.status).toBe('running')
  await expect(panel.locator('.experiment-verdict')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('live-running.png'), fullPage: true })
  const state = await (await finished).json()
  writeFileSync(testInfo.outputPath('live-result.json'), JSON.stringify(state, null, 2))
  expect(state.status, state.error || 'experiment did not finish').toBe('succeeded')
  expect(state.report.run_id).toBe(task.run_id)
  expect(Date.parse(state.report.generated_at)).toBeGreaterThanOrEqual(Date.parse(task.started_at))
  expect(state.report.configurations).toHaveLength(3)
  for (const config of state.report.configurations) {
    expect(config.records).toHaveLength(61)
    expect(config.business_snapshot_unchanged).toBe(true)
  }
  await expect(panel.locator('.experiment-verdict')).toContainText(state.report.accepted ? '达到目标' : '未达到全部验收目标')
  await expect(panel).toContainText(task.run_id)
  await panel.getByRole('button', { name: '演示危险写入', exact: true }).click()
  await expect(panel.getByTestId('probe-gateway_only')).toContainText('绕过成功')
  await expect(panel.getByTestId('probe-gateway_and_database')).toContainText('42501')
  await panel.getByRole('heading', { name: '场景二：绕过网关，直接访问数据库' }).evaluate(el => el.scrollIntoView({ block: 'start' }))
  await page.screenshot({ path: testInfo.outputPath('live-result.png') })
})
