import { test, expect } from '@playwright/test'

test('attack replay stays separate from the live protected business query', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '进入演示' }).click()
  await page.locator('.project-card').filter({ hasText: 'D contract A' }).getByRole('button', { name: '进入工作台' }).click()
  await page.getByRole('link', { name: '查询与审计', exact: true }).click()
  const panel = page.getByRole('region', { name: '攻防实验历史回放' })
  await expect(panel).toContainText('完整配置：本次固定实验达到目标')
  const questions: string[] = []
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/secure-queries')) questions.push(request.postDataJSON().question) })
  await panel.getByRole('button', { name: '演示危险写入', exact: true }).click()
  await expect(panel.getByTestId('experiment-no_gateway')).toContainText('越界候选已执行')
  await expect(panel.getByTestId('experiment-gateway_and_database')).toContainText('策略拒绝，未执行')
  await panel.getByRole('button', { name: '演示论文提示注入', exact: true }).click()
  await panel.getByTestId('experiment-gateway_and_database').getByText('查看本例原始结果与审计', { exact: true }).click()
  await expect(panel.getByTestId('experiment-gateway_and_database')).toContainText('security.query.finished')
  expect(questions).toEqual([])
  await page.getByLabel('统计问题').fill('按年份统计论文数量')
  const response = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/secure-queries'))
  await page.getByRole('button', { name: '提交查询', exact: true }).click()
  expect(await (await response).json()).toMatchObject({ decision: 'allowed', execution_status: 'executed', mock: false })
  expect(questions).toEqual(['按年份统计论文数量'])
})
