<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import {
  deleteCGMReportAPI,
  importCGMReportAPI,
  listCGMReportsAPI,
  type CGMReportSummary,
} from "../../../apis/cgm"

const reports = ref<CGMReportSummary[]>([])
const loadingList = ref(false)
const uploadingName = ref("")
const uploadingPercent = ref(0)
const isDragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const ACCEPTED = ".pdf,.png,.jpg,.jpeg,.csv,.xlsx"
const MAX_SIZE_MB = 50

const fetchReports = async () => {
  loadingList.value = true
  try {
    const response = await listCGMReportsAPI(20, 0)
    if (response.data.status_code !== 200) {
      throw new Error(response.data.status_message || "获取历史报告失败")
    }
    reports.value = response.data.data || []
  } catch (error: any) {
    ElMessage.error(error?.message || "获取历史报告失败")
  } finally {
    loadingList.value = false
  }
}

const formatRange = (item: CGMReportSummary) => {
  if (!item.monitoring_end_date) {
    return item.create_time ? new Date(item.create_time).toLocaleDateString("zh-CN") : "—"
  }
  const end = new Date(item.monitoring_end_date)
  const dateText = `${end.getMonth() + 1} 月 ${end.getDate()} 日`
  const days = item.monitoring_days ? `${item.monitoring_days} 天范围` : ""
  return days ? `${dateText} · ${days}` : dateText
}

const fileExtBadge = (item: CGMReportSummary) => {
  const name = (item.file_name || "").toLowerCase()
  if (name.endsWith(".pdf")) return "PDF"
  if (name.endsWith(".csv")) return "CSV"
  if (name.endsWith(".xlsx")) return "XLSX"
  if (/\.(png|jpe?g|gif|webp)$/.test(name)) return "IMG"
  return "FILE"
}

const fileBadgeClass = (item: CGMReportSummary) => {
  const ext = fileExtBadge(item)
  if (ext === "CSV") return "badge-mint"
  if (ext === "XLSX") return "badge-mint"
  if (ext === "IMG") return "badge-butter"
  return "badge-coral"
}

const statusText = (item: CGMReportSummary) => {
  if (item.parse_status === "success") {
    return item.tir_pct != null ? `已解析 · TIR ${Number(item.tir_pct).toFixed(0)}%` : "已解析"
  }
  if (item.parse_status === "failed") {
    return item.parse_error ? `解析失败 · ${item.parse_error.slice(0, 24)}` : "解析失败"
  }
  return "解析中"
}

const statusClass = (item: CGMReportSummary) => {
  if (item.parse_status === "success") return "stat-good"
  if (item.parse_status === "failed") return "stat-risk"
  return "stat-warn"
}

const triggerSelectFile = () => {
  fileInput.value?.click()
}

const handleFileSelected = (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) {
    void uploadFile(file)
  }
  input.value = ""
}

const handleDrop = (event: DragEvent) => {
  event.preventDefault()
  isDragOver.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) {
    void uploadFile(file)
  }
}

const onDragEnter = (event: DragEvent) => {
  event.preventDefault()
  isDragOver.value = true
}
const onDragLeave = (event: DragEvent) => {
  event.preventDefault()
  isDragOver.value = false
}

const uploadFile = async (file: File) => {
  if (file.size > MAX_SIZE_MB * 1024 * 1024) {
    ElMessage.error(`文件超过 ${MAX_SIZE_MB} MB 上限`)
    return
  }

  uploadingName.value = file.name
  uploadingPercent.value = 0

  const formData = new FormData()
  formData.append("file", file)

  try {
    const xhr = await new Promise<XMLHttpRequest>((resolve, reject) => {
      const req = new XMLHttpRequest()
      req.open("POST", "/api/v1/upload")
      const token = localStorage.getItem("token") || ""
      if (token) {
        req.setRequestHeader("Authorization", `Bearer ${token}`)
      }
      req.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) {
          uploadingPercent.value = Math.round((e.loaded / e.total) * 90)
        }
      })
      req.onload = () => {
        if (req.status >= 200 && req.status < 300) {
          resolve(req)
        } else {
          reject(new Error(`上传失败 (${req.status})`))
        }
      }
      req.onerror = () => reject(new Error("上传过程出错"))
      req.send(formData)
    })

    const parsed = JSON.parse(xhr.responseText || "{}")
    const fileUrl: string | undefined = parsed?.data
    if (!fileUrl) {
      throw new Error(parsed?.status_message || "未获取到文件地址")
    }

    uploadingPercent.value = 95
    const importResp = await importCGMReportAPI(fileUrl, file.name)
    if (importResp.data.status_code !== 200) {
      throw new Error(importResp.data.status_message || "CGM 报告解析失败")
    }
    uploadingPercent.value = 100
    ElMessage.success("CGM 报告上传并解析成功")
    await fetchReports()
  } catch (error: any) {
    ElMessage.error(error?.message || "上传失败")
  } finally {
    setTimeout(() => {
      uploadingName.value = ""
      uploadingPercent.value = 0
    }, 600)
  }
}

const openReportFile = (item: CGMReportSummary) => {
  if (item.file_url) {
    window.open(item.file_url, "_blank", "noopener")
  }
}

const removeReport = async (item: CGMReportSummary) => {
  try {
    await ElMessageBox.confirm(
      `删除 ${item.file_name || "该报告"}？该操作不可恢复。`,
      "确认删除",
      {
        confirmButtonText: "删除",
        cancelButtonText: "取消",
        type: "warning",
        customClass: "delete-confirm-dialog",
      }
    )
  } catch {
    return
  }

  try {
    const response = await deleteCGMReportAPI(item.id)
    if (response.data.status_code !== 200) {
      throw new Error(response.data.status_message || "删除失败")
    }
    ElMessage.success("已删除")
    await fetchReports()
  } catch (error: any) {
    ElMessage.error(error?.message || "删除失败")
  }
}

const isUploading = computed(() => !!uploadingName.value)

onMounted(fetchReports)
</script>

<template>
  <div class="upload-page">
    <header class="topbar">
      <div class="topbar-title">
        <h1>上传 CGM 报告</h1>
        <p>糖糖会在 30 秒内读完并给你反馈</p>
      </div>
    </header>

    <div class="scroll-area">
      <section class="upload-hero">
        <h2>把你的 CGM 报告交给糖 ✨</h2>
        <p class="lead">
          上传你设备导出的数据或截图，糖糖会自动识别
          <em>TIR / TAR / TBR、餐后峰值、夜间波动</em>，并挑重点讲给你听。
        </p>
      </section>

      <section class="upload-hero">
        <label
          class="dropzone"
          :class="{ dragover: isDragOver }"
          @dragenter="onDragEnter"
          @dragover.prevent
          @dragleave="onDragLeave"
          @drop="handleDrop"
        >
          <input
            ref="fileInput"
            type="file"
            :accept="ACCEPTED"
            class="file-input"
            @change="handleFileSelected"
          />
          <div class="dz-inner">
            <div class="dz-icon">
              <svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="url(#dz-g)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <defs>
                  <linearGradient id="dz-g" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0" stop-color="#FF8E70" />
                    <stop offset="1" stop-color="#FFB07A" />
                  </linearGradient>
                </defs>
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
            </div>
            <div class="dz-title">把文件拖到这里就好</div>
            <div class="dz-sub">或点击下方按钮选择本地文件</div>
            <button class="btn btn-primary" type="button" @click.prevent="triggerSelectFile">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
              选择文件
            </button>
            <div class="dz-formats">
              <span class="fmt-tag">PDF</span>
              <span class="fmt-tag">PNG · JPG</span>
              <span class="fmt-tag">CSV</span>
              <span class="fmt-tag">XLSX</span>
              <span class="fmt-tag">≤ {{ MAX_SIZE_MB }} MB</span>
            </div>
          </div>
        </label>
      </section>

      <section v-if="isUploading" class="upload-section">
        <h2 class="section-title">正在上传</h2>
        <div class="uploading">
          <div class="spinner"></div>
          <div class="up-info">
            <div class="up-row">
              <span class="up-name">{{ uploadingName }}</span>
              <span class="up-pct">{{ uploadingPercent }}%</span>
            </div>
            <div class="progress-bar">
              <span :style="{ width: uploadingPercent + '%' }"></span>
            </div>
          </div>
        </div>
      </section>

      <section class="upload-section">
        <h2 class="section-title">报告从哪里来？</h2>
        <div class="guides">
          <div class="guide-card">
            <div class="guide-num n1">1</div>
            <h4>雅培 LibreView</h4>
            <p>登录 libreview.com → "葡萄糖" → 导出 PDF 或 CSV，时间范围选 7 / 14 / 30 天均可。</p>
          </div>
          <div class="guide-card">
            <div class="guide-num n2">2</div>
            <h4>德康 Clarity</h4>
            <p>App 内点击"分享" → 选择"PDF 报告"，发到电脑上传；或直接传截图也行。</p>
          </div>
          <div class="guide-card">
            <div class="guide-num n3">3</div>
            <h4>截图 / 手机相册</h4>
            <p>找不到导出？直接发 CGM App 的"AGP 图"或"每日曲线"截图，糖糖也能读懂。</p>
          </div>
        </div>
      </section>

      <section class="upload-section last-section">
        <h2 class="section-title">最近上传的报告</h2>
        <div v-if="loadingList" class="empty-list">正在加载…</div>
        <div v-else-if="reports.length === 0" class="empty-list">
          还没有上传过报告，把上面的卡片当起点吧。
        </div>
        <div v-else class="recent-list">
          <div v-for="item in reports" :key="item.id" class="recent-item">
            <div class="file-icon" :class="fileBadgeClass(item)">{{ fileExtBadge(item) }}</div>
            <div class="recent-info">
              <div class="recent-name" :title="item.file_name || ''">
                {{ item.file_name || "未命名报告" }}
              </div>
              <div class="recent-meta">
                <span>{{ formatRange(item) }}</span>
                <span class="dot"></span>
                <span class="recent-stat" :class="statusClass(item)">
                  {{ statusText(item) }}
                </span>
              </div>
            </div>
            <div class="recent-actions">
              <button class="icon-btn" :disabled="!item.file_url" @click="openReportFile(item)" aria-label="查看原文件">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
              </button>
              <button class="icon-btn" aria-label="删除" @click="removeReport(item)">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="3 6 5 6 21 6" />
                  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                  <path d="M10 11v6M14 11v6" />
                  <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.upload-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  box-shadow: var(--sh-2);
}

.topbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 26px;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(180deg, #FFFAF3 0%, #FFFFFF 100%);

  .topbar-title {
    flex: 1;
    min-width: 0;
  }

  h1 {
    margin: 0;
    font-size: 17px;
    font-weight: 700;
    color: var(--ink);
  }

  p {
    margin: 4px 0 0;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 2px 10px;
    border-radius: var(--r-pill);
    background: var(--mint-tint);
    color: var(--good-text);
    font-size: 11.5px;
    font-weight: 600;
  }
}

.scroll-area {
  flex: 1;
  overflow-y: auto;
  padding: 28px 0 40px;
  background:
    radial-gradient(circle at 100% 0%, rgba(255,176,130,.08) 0%, transparent 40%),
    radial-gradient(circle at 0% 100%, rgba(92,200,166,.06) 0%, transparent 40%);
}

.upload-hero {
  max-width: 760px;
  margin: 0 auto;
  padding: 0 32px;

  h2 {
    margin: 0 0 8px;
    font-size: 26px;
    font-weight: 800;
    color: var(--ink);
    letter-spacing: 0.5px;
  }

  .lead {
    color: var(--ink-2);
    font-size: 14.5px;
    line-height: 1.7;
    max-width: 580px;
    em {
      color: var(--coral-deep);
      font-style: normal;
      font-weight: 600;
    }
  }
}

.upload-hero + .upload-hero {
  margin-top: 28px;
}

.dropzone {
  display: block;
  background: linear-gradient(140deg, #FFF7EF 0%, #FFF1E2 50%, #FFEEDE 100%);
  border: 2px dashed rgba(255, 126, 95, .35);
  border-radius: var(--r-xl);
  padding: 48px 40px;
  text-align: center;
  cursor: pointer;
  transition: all .2s ease;
  position: relative;
  overflow: hidden;

  &:hover,
  &.dragover {
    border-color: var(--coral);
    background: linear-gradient(140deg, #FFEEDE 0%, #FFE3D0 100%);
    transform: translateY(-2px);
  }

  &::before,
  &::after {
    content: "";
    position: absolute;
    border-radius: 50%;
    filter: blur(20px);
    opacity: .6;
    pointer-events: none;
  }
  &::before { width: 100px; height: 100px; background: var(--butter); top: -20px; left: -20px; }
  &::after { width: 140px; height: 140px; background: var(--mint-soft); bottom: -40px; right: -30px; }
}

.file-input {
  display: none;
}

.dz-inner {
  position: relative;
  z-index: 1;
}

.dz-icon {
  width: 80px;
  height: 80px;
  margin: 0 auto 16px;
  border-radius: 24px;
  background: #fff;
  display: grid;
  place-items: center;
  box-shadow: 0 12px 30px -8px rgba(255, 126, 95, .35);
}

.dz-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--ink);
  margin-bottom: 4px;
}

.dz-sub {
  color: var(--ink-3);
  font-size: 13px;
  margin-bottom: 16px;
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 18px;
  border: 0;
  border-radius: var(--r-md);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: transform .12s, box-shadow .15s;
}

.btn-primary {
  background: linear-gradient(135deg, #FF8B6F 0%, #FF6F4E 100%);
  color: #fff;
  box-shadow: var(--sh-coral);

  &:hover {
    transform: translateY(-1px);
    box-shadow: 0 10px 24px -6px rgba(255, 126, 95, .7);
  }
}

.dz-formats {
  display: inline-flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 18px;
}

.fmt-tag {
  padding: 4px 10px;
  background: rgba(255,255,255,.7);
  border: 1px solid var(--border);
  border-radius: var(--r-pill);
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-2);
  font-family: var(--font-num);
}

.upload-section {
  max-width: 760px;
  margin: 36px auto 0;
  padding: 0 32px;
}

.last-section {
  padding-bottom: 32px;
}

.section-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  margin: 0 0 14px;
  display: flex;
  align-items: center;
  gap: 8px;

  &::before {
    content: "";
    width: 4px;
    height: 14px;
    background: var(--coral);
    border-radius: 999px;
  }
}

.guides {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.guide-card {
  padding: 16px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: transform .15s, box-shadow .15s;

  &:hover {
    transform: translateY(-2px);
    box-shadow: var(--sh-2);
  }

  h4 {
    margin: 0;
    font-size: 14px;
    font-weight: 700;
    color: var(--ink);
  }

  p {
    margin: 0;
    color: var(--ink-3);
    font-size: 12.5px;
    line-height: 1.6;
  }
}

.guide-num {
  width: 26px;
  height: 26px;
  border-radius: 9px;
  font-family: var(--font-num);
  font-weight: 700;
  font-size: 13px;
  display: grid;
  place-items: center;

  &.n1 { background: var(--coral-tint); color: var(--coral-deep); }
  &.n2 { background: var(--mint-tint); color: var(--good-text); }
  &.n3 { background: #FFF1D9; color: var(--warn-text); }
}

.recent-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.empty-list {
  padding: 24px;
  text-align: center;
  color: var(--ink-3);
  background: var(--card-tint);
  border: 1px dashed var(--border-strong);
  border-radius: var(--r-md);
  font-size: 13.5px;
}

.recent-item {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 14px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  transition: all .15s;

  &:hover {
    border-color: var(--coral-soft);
    background: var(--card-tint);
  }
}

.file-icon {
  width: 38px;
  height: 48px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  font-family: var(--font-num);
  font-weight: 700;
  font-size: 10px;
  letter-spacing: 0.5px;
  position: relative;

  &::after {
    content: "";
    position: absolute;
    width: 0;
    height: 0;
    top: 0;
    right: 0;
    border-style: solid;
    border-width: 0 10px 10px 0;
    border-color: transparent #fff transparent transparent;
  }

  &.badge-coral {
    background: linear-gradient(135deg, #FFF1E2 0%, #FFD6C8 100%);
    color: var(--coral-deep);
  }
  &.badge-mint {
    background: linear-gradient(135deg, #E8F7F0 0%, #C9EFE0 100%);
    color: var(--good-text);
  }
  &.badge-butter {
    background: linear-gradient(135deg, #FFF1D9 0%, #FFE2A8 100%);
    color: var(--warn-text);
  }
}

.recent-info {
  flex: 1;
  min-width: 0;
}

.recent-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recent-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--ink-3);
  font-family: var(--font-num);

  .dot {
    width: 3px;
    height: 3px;
    background: var(--ink-4);
    border-radius: 50%;
  }
}

.recent-stat {
  font-weight: 600;

  &.stat-good { color: var(--good); }
  &.stat-warn { color: var(--warn); }
  &.stat-risk { color: var(--risk); }
}

.recent-actions {
  display: flex;
  gap: 6px;
}

.icon-btn {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--ink-2);
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: background .12s, color .12s;

  &:hover {
    background: rgba(74, 47, 25, .06);
    color: var(--ink);
  }
  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
}

.uploading {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  background: var(--mint-tint);
  border: 1px solid rgba(92, 200, 166, .25);
  border-radius: var(--r-md);
  font-size: 13.5px;
  color: var(--good-text);
}

.spinner {
  width: 22px;
  height: 22px;
  border: 3px solid rgba(92, 200, 166, .25);
  border-top-color: var(--mint);
  border-radius: 50%;
  animation: spin .9s linear infinite;
  flex-shrink: 0;
}

@keyframes spin { to { transform: rotate(360deg); } }

.up-info { flex: 1; min-width: 0; }

.up-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
  gap: 10px;
}

.up-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.up-pct {
  font-family: var(--font-num);
  flex-shrink: 0;
}

.progress-bar {
  height: 6px;
  background: rgba(92, 200, 166, .2);
  border-radius: 999px;
  overflow: hidden;

  > span {
    display: block;
    height: 100%;
    background: linear-gradient(90deg, var(--mint), #4FB689);
    border-radius: 999px;
    transition: width .3s ease;
  }
}

@media (max-width: 900px) {
  .guides { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 600px) {
  .guides { grid-template-columns: 1fr; }
  .upload-hero,
  .upload-section { padding-left: 20px; padding-right: 20px; }
}
</style>
