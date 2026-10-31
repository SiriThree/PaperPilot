import { test, expect, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'

const fixture = () => JSON.parse(readFileSync('public/security-experiment.json', 'utf8'))
async function open(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  await expect(page.getByRole('heading', { name: '你的研究项目' })).toBeVisible()
  await page.locator('.project-card').first().getByRole('button', { name: '进入工作台' }).click()
  await page.getByRole('link', { name: '查询与审计', exact: true }).click()
  return page.getByRole('region', { name: '攻防实验演示' })
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
    await expect(panel).toContainText('网关已拦截，数据库层未触发')
    for (const id of ['gateway_only', 'gateway_and_database']) {
      const path = panel.getByTestId(`experiment-${id}`).getByLabel('本例执行路径')
      await expect(path).toContainText('已拒绝')
      await expect(path).toContainText('未到达')
    }
    const direct = panel.getByRole('region', { name: '绕过网关的数据库直接访问' })
    await expect(direct.getByRole('heading', { name: '场景二：绕过网关，直接访问数据库' })).toBeVisible()
    await expect(direct.getByLabel('直接访问目标')).toHaveValue('foreign_view')
    await expect(direct.getByTestId('probe-gateway_only')).toContainText('绕过成功')
    await expect(direct.getByTestId('probe-gateway_only')).toContainText('1 行')
    await expect(direct.getByTestId('probe-gateway_and_database')).toContainText('数据库按项目过滤')
    await expect(direct.getByTestId('probe-gateway_and_database')).toContainText('0 行')
    await panel.getByRole('heading', { name: '场景一：经过网关的查询入口' }).evaluate(el => el.scrollIntoView({ block: 'start' }))
    await page.screenshot({ path: testInfo.outputPath('gateway-path.png'), animations: 'disabled' })
    await direct.getByRole('heading', { name: '场景二：绕过网关，直接访问数据库' }).evaluate(el => el.scrollIntoView({ block: 'start' }))
    await page.screenshot({ path: testInfo.outputPath('database-comparison.png'), animations: 'disabled' })
    await direct.getByLabel('直接访问目标').selectOption('base_table')
    await panel.getByRole('button', { name: '演示论文提示注入', exact: true }).click()
    await expect(direct.getByLabel('直接访问目标')).toHaveValue('foreign_view')
    await panel.getByRole('button', { name: '演示危险写入', exact: true }).click()
    await expect(panel.getByLabel('固定候选 SQL 模板')).toContainText('UPDATE public.papers')
    await expect(direct.getByLabel('直接访问目标')).toHaveValue('write')
    await expect(direct).toContainText('并非上方同一条 SQL 的重放')
    await expect(direct.getByTestId('probe-gateway_only')).toContainText('绕过成功')
    await expect(direct.getByTestId('probe-gateway_and_database')).toContainText('原因代码：42501')
    await direct.getByTestId('probe-gateway_and_database').getByText('查看直接访问的实际 SQL', { exact: true }).click()
    await expect(direct.getByTestId('probe-gateway_and_database').locator('pre')).toContainText('UPDATE public.projects')
    await panel.getByRole('button', { name: '演示论文提示注入', exact: true }).click()
    await expect(panel).toContainText('不可信论文内容')
    await expect(direct.getByLabel('直接访问目标')).toHaveValue('foreign_view')
    await panel.getByTestId('experiment-gateway_and_database').getByText('查看本例原始结果与审计', { exact: true }).click()
    await expect(panel.getByTestId('experiment-gateway_and_database')).toContainText('security.query.finished')
    await panel.getByRole('button', { name: '演示正常查询', exact: true }).click()
    for (const id of ['no_gateway', 'gateway_only', 'gateway_and_database']) await expect(panel.getByTestId(`experiment-${id}`)).toContainText('正常结果一致')
    await expect(panel.getByTestId('experiment-gateway_and_database').getByLabel('本例执行路径')).toContainText('已执行（受限身份）')
    await expect(panel).not.toContainText('网关已拦截，数据库层未触发')
    await expect(direct).toContainText('正常查询无需绕过网关')
    await direct.getByLabel('直接访问目标').selectOption('base_table')
    await expect(direct.getByTestId('probe-gateway_only')).toContainText('4 行')
    await expect(direct.getByTestId('probe-gateway_and_database')).toContainText('数据库权限拒绝')
    await panel.getByLabel('用例类型').selectOption('cross_project')
    await expect(direct.getByLabel('直接访问目标')).toHaveValue('foreign_view')
    await direct.getByLabel('直接访问目标').selectOption('base_table')
    await panel.getByLabel('固定用例').selectOption('cross_project-02')
    await expect(direct.getByLabel('直接访问目标')).toHaveValue('foreign_view')
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('security-experiment.png'), fullPage: true, animations: 'disabled' })
    expect(queries).toBe(0)
  })
}

test('an undetermined failed query does not claim the gateway or database blocked it', async ({ page }) => {
  await page.route('**/security-experiment.json', async route => {
    const report = fixture(), record = report.configurations[2].records.find((r: { case: string }) => r.case === 'cross_project-01')
    record.decision = 'undetermined'
    record.execution_status = 'failed'
    record.reason = 'SECURITY_AUDIT_UNAVAILABLE'
    await route.fulfill({ json: report })
  })
  const panel = await open(page), path = panel.getByTestId('experiment-gateway_and_database').getByLabel('本例执行路径')
  await expect(path).toContainText('未确认执行完成')
  await expect(path).not.toContainText('已拒绝')
  await expect(path).not.toContainText('未到达')
  await expect(panel).not.toContainText('网关已拦截，数据库层未触发')
})

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

test('live run hides history, reports progress and downloads only its fresh result', async ({ page }) => {
  const runId = 'a0123456-0123-4123-8123-012345678901', startedAt = new Date().toISOString()
  let started = false, finished = false, posts = 0
  await page.route('**/security-experiments', async route => {
    if (route.request().method() === 'POST') { expect(route.request().postDataJSON()).toEqual({}); posts++; started = true }
    const report = fixture(); report.run_id = runId; report.generated_at = new Date().toISOString()
    await route.fulfill({ json: { enabled: true, busy: started && !finished, status: !started ? 'idle' : finished ? 'succeeded' : 'running', phase: finished ? 'finished' : 'gateway_only', run_id: started ? runId : null, started_at: startedAt, report: finished ? report : null } })
  })
  const panel = await open(page)
  await expect(panel).toContainText('历史结果回放')
  await panel.getByRole('button', { name: '开始实时实验', exact: true }).click()
  await expect(panel).toContainText('阶段 2/3')
  await expect(panel.locator('.experiment-verdict')).toHaveCount(0)
  finished = true
  await panel.getByRole('button', { name: '查询运行进度', exact: true }).click()
  await expect(panel).toContainText('完整配置：本次固定实验达到目标')
  await expect(panel).toContainText(runId)
  await panel.getByText('测量口径、来源与现场复跑', { exact: true }).click()
  const download = page.waitForEvent('download')
  await panel.getByRole('button', { name: '下载当前结果 JSON', exact: true }).click()
  const file = await download
  expect(JSON.parse(readFileSync((await file.path())!, 'utf8')).run_id).toBe(runId)
  expect(posts).toBe(1)
})

for (const kind of ['docker-failed', 'wrong-result']) {
  test(`live ${kind} never falls back to a passing historical report`, async ({ page }) => {
    const runId = 'b0123456-0123-4123-8123-012345678902'
    let started = false, finished = false
    await page.route('**/security-experiments', async route => {
      if (route.request().method() === 'POST') started = true
      await route.fulfill({ json: { enabled: true, busy: started && !finished, status: !started ? 'idle' : !finished ? 'running' : kind === 'docker-failed' ? 'failed' : 'succeeded', run_id: started ? runId : null, phase: 'starting', started_at: new Date().toISOString(), error: kind === 'docker-failed' ? 'DOCKER_CONTAINER_FAILED' : null, report: finished ? fixture() : null } })
    })
    const panel = await open(page)
    await expect(panel).toContainText('完整配置：本次固定实验达到目标')
    await panel.getByRole('button', { name: '开始实时实验', exact: true }).click()
    finished = true
    await panel.getByRole('button', { name: '查询运行进度', exact: true }).click()
    await expect(panel.locator('.experiment-live-error')).toBeVisible()
    if (kind === 'docker-failed') await expect(panel).toContainText('DOCKER_CONTAINER_FAILED')
    await expect(panel.locator('.experiment-verdict')).toHaveCount(0)
    await expect(panel).toContainText('历史通过记录不会作为本次结果展示')
  })
}

test('opening an active run resumes status polling without starting another experiment', async ({ page }) => {
  let posts = 0
  await page.route('**/security-experiments', async route => {
    if (route.request().method() === 'POST') posts++
    await route.fulfill({ json: { enabled: true, busy: true, status: 'running', run_id: 'c0123456-0123-4123-8123-012345678903', phase: 'gateway_and_database', started_at: new Date().toISOString() } })
  })
  const panel = await open(page)
  await expect(panel).toContainText('阶段 3/3')
  await expect(panel.locator('.experiment-verdict')).toHaveCount(0)
  await expect(panel.getByRole('button', { name: '实验运行中' })).toBeDisabled()
  expect(posts).toBe(0)
})
