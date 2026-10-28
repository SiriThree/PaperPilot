// Convenience checks only. The server remains authoritative for every request.
export const MAX_PDF_BYTES = 20 * 1024 * 1024
export async function pdfProblem(file: File): Promise<string> {
  if (!file.size) return '文件为空，请选择包含内容的 PDF。'
  if (file.size > MAX_PDF_BYTES) return '单篇文件上限为 20 MiB，请选择较小的 PDF。'
  if (await file.slice(0, 5).text() !== '%PDF-') return '文件头不是 PDF，请重新选择文件；正文有效性仍由服务端处理。'
  return ''
}
export function yearProblem(from?: number, to?: number): string {
  if ([from, to].some(year => year != null && (!Number.isInteger(year) || year < 1000 || year > 3000))) return '年份须为 1000 至 3000 之间的整数。'
  return from != null && to != null && from > to ? '起始年份不能晚于结束年份。' : ''
}
