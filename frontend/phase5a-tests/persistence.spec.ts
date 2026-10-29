import { test, expect } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'

const port = Number(process.env.PAPERPILOT_PHASE5A_API_PORT || 8184)
const python = path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
let server: ChildProcess | undefined

async function start() {
  server = spawn(python, ['-m', 'uvicorn', 'app.dev_composition:create_app', '--factory', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', String(port), '--no-access-log'], { cwd: '..', env: process.env, stdio: 'ignore', windowsHide: true })
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error('Isolated backend exited before readiness')
    try { if ((await fetch(`http://127.0.0.1:${port}/api/v1/health`)).ok) return } catch { /* waiting for own server */ }
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  throw new Error('Isolated backend did not become ready')
}
async function stop() {
  if (server && server.exitCode === null) {
    const ended = new Promise<void>(resolve => server!.once('exit', () => resolve()))
    server.kill()
    await ended
  }
  server = undefined
}

test('PostgreSQL A with Mock C/D: upload, refresh and backend restart preserve task and paper', async ({ page }) => {
  await start()
  try {
    await page.goto('/')
    await page.getByRole('button', { name: '进入演示' }).click()
    await page.getByLabel('新项目名称').fill('Phase 5A browser')
    await page.getByRole('button', { name: '创建项目' }).click()
    await page.getByLabel('选择 PDF 文件').setInputFiles(process.env.PAPERPILOT_PHASE5A_PDF!)
    await page.getByRole('button', { name: '上传并模拟处理' }).click()
    await expect(page.getByRole('region', { name: '任务恢复' })).toContainText('任务：succeeded')
    const id = await page.getByTestId('recovery-task-id').innerText()
    const projectPath = new URL(page.url()).pathname
    await page.reload()
    await expect(page.getByTestId('recovery-task-id')).toHaveText(id)
    await expect(page.getByText('数据适配器 · 待验收')).toBeVisible()
    await page.getByRole('link', { name: '研究', exact: true }).click()
    await page.getByLabel('报告正文').fill('Phase 5A 已保存的报告')
    await page.getByRole('button', { name: '保存新版本' }).click()
    await expect(page.getByRole('button', { name: '版本 1', exact: true })).toBeVisible()
    await page.getByRole('link', { name: '文献', exact: true }).click()
    await expect(page.getByTestId('recovery-task-id')).toHaveText(id)
    await stop()
    await page.getByRole('button', { name: '重新查询任务' }).click()
    await expect(page.getByRole('region', { name: '任务恢复' })).toContainText('当前任务状态未知')
    await start()
    await page.reload()
    await page.getByRole('button', { name: '进入演示' }).click()
    await expect(page).toHaveURL(new RegExp(`${projectPath}$`))
    await expect(page.getByTestId('recovery-task-id')).toHaveText(id)
    await expect(page.getByRole('region', { name: '任务恢复' })).toContainText('任务：succeeded')
    await expect(page.getByText('text', { exact: true })).toBeVisible()
    await expect(page.getByText('当前仅模拟处理，不提取真实正文。')).toBeVisible()
    await page.getByRole('link', { name: '研究', exact: true }).click()
    await page.getByRole('button', { name: /研究综述草稿 · v1/ }).click()
    await expect(page.getByLabel('报告正文')).toHaveValue('Phase 5A 已保存的报告')
  } finally { await stop() }
})
