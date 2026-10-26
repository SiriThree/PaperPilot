import { test, expect, type Page } from '@playwright/test'

async function project(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  await page.getByLabel('新项目名称').fill('任务恢复测试')
  await page.getByRole('button', { name: '创建项目' }).click()
  await expect(page.getByRole('heading', { name: '文献工作台' })).toBeVisible()
}
const fixture = { name: 'recovery.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nRecovery fixture') }
async function choose(page: Page) { await page.getByLabel('选择 PDF 文件').setInputFiles(fixture) }
async function upload(page: Page) {
  await choose(page)
  await page.getByRole('button', { name: '上传并模拟处理' }).click()
  await expect(page.getByText('任务：succeeded', { exact: false })).toBeVisible()
}
const panel = (page: Page) => page.getByRole('region', { name: '任务恢复' })
async function research(page: Page) { await upload(page); await page.getByRole('link', { name: '研究', exact: true }).click() }
function countCreates(page: Page, suffix: string) {
  const requests: string[] = []
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith(suffix)) requests.push(request.url()) })
  return requests
}

for (const kind of ['process', 'answer', 'evidence', 'draft'] as const) {
  test(`${kind}: refresh restores the same completed task without generation`, async ({ page }) => {
    await project(page)
    if (kind !== 'process') await research(page)
    const suffix = { process: '/papers', answer: '/answers', evidence: '/evidence', draft: '/report-drafts' }[kind]
    const creates = countCreates(page, suffix)
    if (kind === 'process') await upload(page)
    if (kind === 'answer') await page.getByRole('button', { name: '生成 Mock 回答' }).click()
    if (kind === 'draft') await page.getByRole('button', { name: '生成 Mock 草稿' }).click()
    if (kind === 'evidence') {
      await page.getByRole('button', { name: '检索', exact: true }).click()
      await page.getByRole('button', { name: '提取 Mock 证据' }).click()
    }
    await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
    const id = await page.getByTestId('recovery-task-id').innerText()
    await page.reload()
    await expect(page.getByTestId('recovery-task-id')).toHaveText(id)
    await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
    if (kind === 'answer') await expect(page.getByRole('heading', { name: /证据问答/ })).toBeVisible()
    if (kind === 'evidence') await expect(page.getByRole('heading', { name: '结构化证据 · AI 提取，待核验（Mock）' })).toBeVisible()
    if (kind === 'draft') await expect(page.getByLabel('报告正文')).toHaveValue(/Mock 合成内容/)
    expect(creates).toHaveLength(1)
    const stored = await page.evaluate(() => Object.keys(sessionStorage).filter(key => key.startsWith('paperpilot.tasks.')).map(key => sessionStorage.getItem(key)))
    for (const value of stored) {
      expect(value).not.toContain('Mock 合成内容')
      expect(value).not.toContain('检索增强生成如何核验证据')
      expect(value).not.toContain('excerpt')
    }
  })
}

test('a timed out query is unknown and manual recovery only GETs the same task', async ({ page }) => {
  await project(page); await research(page)
  const creates = countCreates(page, '/answers')
  let unavailable = true
  await page.route('**/tasks/*', route => unavailable ? route.abort('timedout') : route.continue())
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await expect(panel(page).getByText(/当前任务状态未知/)).toBeVisible()
  const id = await page.getByTestId('recovery-task-id').innerText()
  await expect(panel(page).getByText('任务：failed', { exact: false })).toHaveCount(0)
  unavailable = false
  await page.getByRole('button', { name: '重新查询任务' }).click()
  await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
  await expect(page.getByTestId('recovery-task-id')).toHaveText(id)
  expect(creates).toHaveLength(1)
})

test('60 second polling deadline keeps task identity and never marks failure', async ({ page }) => {
  await project(page); await research(page)
  const creates = countCreates(page, '/answers')
  let waiting = true
  await page.route('**/tasks/*', async route => {
    const response = await route.fetch()
    if (!waiting) return route.fulfill({ response })
    const task = await response.json()
    await route.fulfill({ response, json: { ...task, status: 'processing', attempts: 1, result: null, result_ref: null, error: null } })
  })
  await page.clock.install()
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await expect(panel(page).getByText('任务：processing', { exact: false })).toBeVisible()
  const id = await page.getByTestId('recovery-task-id').innerText()
  await page.clock.fastForward(61000)
  await expect(panel(page).getByText(/等待已超过 60 秒/)).toBeVisible()
  await expect(panel(page).getByText('任务：failed', { exact: false })).toHaveCount(0)
  waiting = false
  await page.getByRole('button', { name: '重新查询任务' }).click()
  await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
  await expect(page.getByTestId('recovery-task-id')).toHaveText(id)
  expect(creates).toHaveLength(1)
})

test('server failure is shown safely after refresh without automatic retry', async ({ page }) => {
  await project(page); await research(page)
  const creates = countCreates(page, '/answers')
  await page.getByLabel('研究问题', { exact: true }).fill('模拟失败')
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await expect(panel(page).getByText('任务：failed', { exact: false })).toBeVisible()
  const id = await page.getByTestId('recovery-task-id').innerText()
  await expect(panel(page).getByText(/AI_UNAVAILABLE/)).toBeVisible()
  await page.reload()
  await expect(page.getByTestId('recovery-task-id')).toHaveText(id)
  await expect(panel(page).getByText(/AI_UNAVAILABLE/)).toBeVisible()
  expect(creates).toHaveLength(1)
})

for (const kind of ['process', 'answer', 'evidence'] as const) {
  test(`${kind}: repeated click dispatches have one creation request`, async ({ page }) => {
    await project(page)
    if (kind === 'process') await choose(page)
    else await research(page)
    if (kind === 'evidence') await page.getByRole('button', { name: '检索', exact: true }).click()
    const creates = countCreates(page, { process: '/papers', answer: '/answers', evidence: '/evidence' }[kind])
    const button = page.getByRole('button', { name: { process: '上传并模拟处理', answer: '生成 Mock 回答', evidence: '提取 Mock 证据' }[kind] })
    await button.evaluate(element => { for (let i = 0; i < 3; i++) element.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
    await button.click()
    await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
    expect(creates).toHaveLength(1)
  })
}

test('unknown answer submission survives refresh and only reconciles explicit identical input', async ({ page }) => {
  await project(page); await research(page)
  const original = 'PRIVATE_RECOVERY_QUESTION_请核对来源'
  await page.getByLabel('研究问题', { exact: true }).fill(original)
  const keys: string[] = []
  let acceptedId = ''
  await page.route('**/answers', async route => {
    keys.push(route.request().headers()['idempotency-key'] || '')
    const response = await route.fetch()
    if (keys.length === 1) { acceptedId = (await response.json()).task_id; await route.abort('failed') }
    else await route.fulfill({ response })
  })
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await expect(panel(page).getByText(/提交结果未知/)).toBeVisible()
  await expect(page.getByRole('button', { name: '生成 Mock 回答' })).toBeDisabled()
  const stored = await page.evaluate(() => Object.keys(sessionStorage).map(key => sessionStorage.getItem(key)).join(''))
  expect(stored).not.toContain(original)
  await page.reload()
  await expect(panel(page).getByText(/提交结果未知/)).toBeVisible()
  expect(keys).toHaveLength(1)
  await page.getByRole('button', { name: '使用原请求核对' }).click()
  await expect(panel(page).getByText(/当前输入与原请求不一致/)).toBeVisible()
  expect(keys).toHaveLength(1)
  await page.getByLabel('研究问题', { exact: true }).fill(original)
  await page.getByRole('button', { name: '使用原请求核对' }).click()
  await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
  await expect(page.getByTestId('recovery-task-id')).toHaveText(acceptedId)
  expect(keys).toHaveLength(2); expect(keys[1]).toBe(keys[0])
})

test('unknown upload requires reselecting the original file after refresh', async ({ page }) => {
  await project(page); await choose(page)
  const keys: string[] = []
  let acceptedId = ''
  await page.route('**/projects/*/papers', async route => {
    if (route.request().method() !== 'POST') return route.continue()
    keys.push(route.request().headers()['idempotency-key'] || '')
    const response = await route.fetch()
    if (keys.length === 1) { acceptedId = (await response.json()).task.task_id; await route.abort('failed') }
    else await route.fulfill({ response })
  })
  await page.getByRole('button', { name: '上传并模拟处理' }).click()
  await expect(panel(page).getByText(/提交结果未知/)).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '使用原请求核对' }).click()
  await expect(panel(page).getByText(/请重新选择原 PDF/)).toBeVisible()
  expect(keys).toHaveLength(1)
  await choose(page)
  await page.getByRole('button', { name: '使用原请求核对' }).click()
  await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
  await expect(page.getByTestId('recovery-task-id')).toHaveText(acceptedId)
  expect(keys).toHaveLength(2); expect(keys[1]).toBe(keys[0])
})

test('a missing task is inaccessible and does not trigger a replacement task', async ({ page }) => {
  await project(page); await research(page)
  const creates = countCreates(page, '/answers')
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
  await page.evaluate(() => {
    const key = Object.keys(sessionStorage).find(value => value.endsWith('.research'))!
    const records = JSON.parse(sessionStorage.getItem(key)!)
    records[0].taskId = crypto.randomUUID()
    sessionStorage.setItem(key, JSON.stringify(records))
  })
  await page.reload()
  await expect(panel(page).getByText(/任务不存在或不可访问/)).toBeVisible()
  expect(creates).toHaveLength(1)
})

test('query failure blocks even a changed-input click until the original task is queried', async ({ page }) => {
  await project(page); await research(page)
  const creates = countCreates(page, '/answers')
  let unavailable = true
  await page.route('**/tasks/*', async route => {
    if (unavailable) await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'TASK_QUERY_UNAVAILABLE', message: '暂时无法查询', request_id: '00000000-0000-4000-8000-000000000001' }) })
    else await route.continue()
  })
  const button = page.getByRole('button', { name: '生成 Mock 回答' })
  await button.click()
  await expect(panel(page).getByText(/当前任务状态未知/)).toBeVisible()
  await page.getByLabel('研究问题', { exact: true }).fill('改变问题不能绕过未确认任务')
  await expect(button).toBeDisabled()
  await button.evaluate(element => element.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  expect(creates).toHaveLength(1)
  unavailable = false
  await page.reload()
  await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
  expect(creates).toHaveLength(1)
})

test('unavailable browser recovery storage prevents sending a new creation request', async ({ page }) => {
  await project(page); await research(page)
  const creates = countCreates(page, '/answers')
  await page.evaluate(() => { Storage.prototype.setItem = () => { throw new DOMException('Blocked', 'QuotaExceededError') } })
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await expect(panel(page).getByText(/无法保存任务恢复记录/)).toBeVisible()
  expect(creates).toHaveLength(0)
})

test('failed-task retry with a lost acknowledgement queries the same ID without creating again', async ({ page }) => {
  await project(page); await research(page)
  const creates = countCreates(page, '/answers')
  await page.getByLabel('研究问题', { exact: true }).fill('模拟失败')
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await expect(panel(page).getByText('任务：failed', { exact: false })).toBeVisible()
  const id = await page.getByTestId('recovery-task-id').innerText()
  let retries = 0
  await page.route('**/tasks/*/retry', async route => { retries++; await route.fetch(); await route.abort('failed') })
  await page.getByRole('button', { name: '重试失败任务' }).evaluate(element => { for (let i = 0; i < 3; i++) element.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
  await expect(panel(page).getByText(/重试或查询的结果暂不明确/)).toBeVisible()
  await page.getByRole('button', { name: '重新查询任务' }).click()
  await expect(panel(page).getByText('任务：failed', { exact: false })).toBeVisible()
  await expect(page.getByTestId('recovery-task-id')).toHaveText(id)
  expect(creates).toHaveLength(1)
  expect(retries).toBe(1)
})

test('selecting a previous completed task reads its original result without generation', async ({ page }) => {
  await project(page); await research(page)
  const drafts = countCreates(page, '/report-drafts')
  const answers = countCreates(page, '/answers')
  await page.getByRole('button', { name: '生成 Mock 草稿' }).click()
  await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
  const id = await page.getByTestId('recovery-task-id').innerText()
  await page.getByLabel('报告正文').fill('临时编辑内容')
  await page.getByRole('button', { name: '生成 Mock 回答' }).click()
  await expect(panel(page).getByText('任务：succeeded', { exact: false })).toBeVisible()
  await panel(page).getByRole('button', { name: /报告草稿/ }).click()
  await expect(page.getByTestId('recovery-task-id')).toHaveText(id)
  await expect(page.getByLabel('报告正文')).toHaveValue(/Mock 合成内容/)
  expect(drafts).toHaveLength(1); expect(answers).toHaveLength(1)
})
