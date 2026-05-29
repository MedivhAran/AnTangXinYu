import { request } from "../utils/request"

export interface CGMReportSummary {
  id: string
  file_name?: string | null
  file_url?: string
  monitoring_start_date?: string
  monitoring_end_date?: string
  monitoring_days?: number
  tir_pct?: number | null
  tar_pct?: number | null
  tbr_pct?: number | null
  parse_status: "pending" | "success" | "failed"
  parse_error?: string | null
  create_time?: string
}

export function importCGMReportAPI(fileUrl: string, fileName: string) {
  return request({
    url: '/api/v1/cgm/import',
    method: 'POST',
    data: {
      file_url: fileUrl,
      file_name: fileName,
    },
    timeout: 60000,
  })
}

export function listCGMReportsAPI(limit = 20, offset = 0) {
  return request({
    url: '/api/v1/cgm/reports',
    method: 'GET',
    params: { limit, offset },
  })
}

export function deleteCGMReportAPI(reportId: string) {
  return request({
    url: `/api/v1/cgm/reports/${reportId}`,
    method: 'DELETE',
  })
}
