<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { ElMessage } from "element-plus"

import histortCard from '../../components/historyCard/histortCard.vue'
import { createDialogAPI, deleteDialogAPI, getDialogListAPI } from "../../apis/history"
import type { DialogCreateType, HistoryListType } from "../../type"
import { useHistoryChatStore } from "../../store/history_chat_msg"
import { useNotificationStore } from "../../store/notification"

const router = useRouter()
const route = useRoute()
const historyChatStore = useHistoryChatStore()
const notificationStore = useNotificationStore()

const dialogs = ref<HistoryListType[]>([])
const loading = ref(false)
const creating = ref(false)
const selectedDialog = ref("")

const ANTANG_AGENT_NAME = "安糖心语"
const ANTANG_AGENT_TYPE = "AnTangAgent"
const isUploadRoute = computed(() => route.path === "/conversation/upload")
const getDialogDisplayTime = (dialog: any) =>
  dialog.last_active_time || dialog.update_time || dialog.create_time || new Date().toISOString()

const filteredDialogs = computed(() =>
  [...dialogs.value].sort(
    (left, right) => new Date(right.createTime).getTime() - new Date(left.createTime).getTime()
  )
)

const getStartOfDay = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()

const getDialogGroupLabel = (dialog: HistoryListType) => {
  const dialogTime = new Date(dialog.createTime)
  const now = new Date()
  const todayStart = getStartOfDay(now)
  const dialogDayStart = getStartOfDay(dialogTime)
  const diffDays = Math.floor((todayStart - dialogDayStart) / (24 * 60 * 60 * 1000))

  if (diffDays <= 0) {
    return "今天"
  }
  if (diffDays < 7) {
    return "本周"
  }
  return "更早"
}

const groupedDialogs = computed(() => {
  const groups = [
    { label: "今天", dialogs: [] as HistoryListType[] },
    { label: "本周", dialogs: [] as HistoryListType[] },
    { label: "更早", dialogs: [] as HistoryListType[] },
  ]
  const groupMap = new Map(groups.map((group) => [group.label, group.dialogs]))

  filteredDialogs.value.forEach((dialog) => {
    groupMap.get(getDialogGroupLabel(dialog))?.push(dialog)
  })

  return groups.filter((group) => group.dialogs.length > 0)
})

const buildDialogName = () => {
  const now = new Date()
  return `${ANTANG_AGENT_NAME} · ${now.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })}`
}

const mapDialog = (dialog: any): HistoryListType => ({
  dialogId: dialog.dialog_id,
  name: dialog.name,
  agent: dialog.agent_name || ANTANG_AGENT_NAME,
  createTime: getDialogDisplayTime(dialog),
  lastActiveTime: getDialogDisplayTime(dialog),
  logo: dialog.agent_logo_url || "",
  agentType: dialog.agent_type || ANTANG_AGENT_TYPE,
})

const selectDialog = (dialogId: string) => {
  const dialog = dialogs.value.find((item) => item.dialogId === dialogId)
  if (!dialog) {
    return
  }

  selectedDialog.value = dialogId
  historyChatStore.dialogId = dialogId
  historyChatStore.name = dialog.name
  historyChatStore.logo = dialog.logo
  historyChatStore.agentType = dialog.agentType || ANTANG_AGENT_TYPE
  // 打开会话即清掉它的未读红点
  void notificationStore.markDialogRead(dialogId)

  router.push({
    path: "/conversation/chatPage",
    query: {
      dialog_id: dialogId,
      agent_type: dialog.agentType || ANTANG_AGENT_TYPE,
    },
  })
}

const fetchDialogs = async () => {
  loading.value = true
  try {
    const response = await getDialogListAPI()
    if (response.data.status_code !== 200) {
      throw new Error(response.data.status_message || "获取会话列表失败")
    }
    dialogs.value = response.data.data
      .map(mapDialog)
      .filter((dialog) => dialog.agentType === ANTANG_AGENT_TYPE)
  } finally {
    loading.value = false
  }
}

const ensureDefaultSelection = () => {
  if (route.path !== "/conversation" || dialogs.value.length === 0) {
    return
  }
  selectDialog(dialogs.value[0].dialogId)
}

const createDialog = async () => {
  if (creating.value) {
    return
  }

  creating.value = true
  try {
    const payload: DialogCreateType = {
      name: buildDialogName(),
      agent_type: ANTANG_AGENT_TYPE,
    }

    const response = await createDialogAPI(payload)
    if (response.data.status_code !== 200) {
      throw new Error(response.data.status_message || "创建会话失败")
    }

    await fetchDialogs()
    const dialogId = response.data.data.dialog_id
    if (dialogId) {
      selectDialog(dialogId)
    }
  } catch (error: any) {
    ElMessage.error(error?.message || "创建会话失败")
  } finally {
    creating.value = false
  }
}

const goUploadPage = () => {
  if (route.path !== "/conversation/upload") {
    router.push("/conversation/upload")
  }
}

const deleteDialog = async (dialogId: string) => {
  try {
    const response = await deleteDialogAPI(dialogId)
    if (response.data.status_code !== 200) {
      throw new Error(response.data.status_message || "删除会话失败")
    }

    if (selectedDialog.value === dialogId) {
      historyChatStore.clear()
      historyChatStore.dialogId = ""
      selectedDialog.value = ""
      router.push("/conversation")
    }
    await fetchDialogs()
    ensureDefaultSelection()
  } catch (error: any) {
    ElMessage.error(error?.message || "删除会话失败")
  }
}

const initialize = async () => {
  try {
    await fetchDialogs()
    const routeDialogId = typeof route.query.dialog_id === "string" ? route.query.dialog_id : ""
    if (routeDialogId) {
      selectedDialog.value = routeDialogId
    } else {
      ensureDefaultSelection()
    }
  } catch (error: any) {
    ElMessage.error(error?.message || "加载安糖会话失败")
  }
}

watch(
  () => route.query.dialog_id,
  (dialogId) => {
    selectedDialog.value = typeof dialogId === "string" ? dialogId : ""
  },
  { immediate: true }
)

watch(
  () => historyChatStore.dialogListRefreshToken,
  async (token, previousToken) => {
    if (token === previousToken) {
      return
    }
    await fetchDialogs()
  }
)

onMounted(() => {
  void initialize()
  notificationStore.startPolling()
})

onUnmounted(() => {
  notificationStore.stopPolling()
})
</script>

<template>
  <div class="conversation-main">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">糖</div>
        <div class="brand-text">
          <div class="brand-name">{{ ANTANG_AGENT_NAME }}</div>
          <div class="brand-tag">陪你走过每一天</div>
        </div>
      </div>

      <div class="side-actions">
        <button class="btn btn-primary" :disabled="creating" @click="createDialog">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
          <span>{{ creating ? "正在创建..." : "新建对话" }}</span>
        </button>

        <a class="upload-card" :class="{ active: isUploadRoute }" @click.prevent="goUploadPage">
          <div class="upload-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" :stroke="isUploadRoute ? '#2C7757' : '#5CC8A6'" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="17 8 12 3 7 8"/>
              <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
          </div>
          <div class="upload-text">
            <div class="upload-title">上传 CGM 报告</div>
            <div class="upload-sub">{{ isUploadRoute ? "当前页面" : "PDF / 图片 / 数据导出" }}</div>
          </div>
        </a>
      </div>

      <div class="history">
        <div v-if="loading" class="empty-state">
          <div class="empty-text">正在加载会话...</div>
        </div>

        <div v-else-if="filteredDialogs.length === 0" class="empty-state">
          <div class="empty-icon">糖</div>
          <div class="empty-text">还没有安糖会话</div>
          <div class="empty-hint">点击上方按钮，开始第一轮对话。</div>
        </div>

        <template v-else>
          <section v-for="group in groupedDialogs" :key="group.label" class="dialog-group">
            <div class="history-section">{{ group.label }}</div>
            <histortCard
              v-for="dialog in group.dialogs"
              :key="dialog.dialogId"
              :item="dialog"
              :unread="notificationStore.unreadDialogIds.has(dialog.dialogId)"
              :class="{ active: selectedDialog === dialog.dialogId }"
              @select="selectDialog(dialog.dialogId)"
              @delete="deleteDialog(dialog.dialogId)"
            />
          </section>
        </template>
      </div>
    </aside>

    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<style lang="scss" scoped>
.conversation-main {
  display: flex;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  gap: 18px;
  padding: 18px;
}

.sidebar {
  width: 296px;
  flex-shrink: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: linear-gradient(140deg, #FFF7EF 0%, #FFEEDE 100%);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--sh-1);
}

.brand-mark {
  width: 42px;
  height: 42px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #FF8E70 0%, #FFB07A 100%);
  box-shadow: 0 6px 14px -4px rgba(255, 126, 95, .55), inset 0 1px 0 rgba(255,255,255,.4);
  color: #fff;
  font-weight: 800;
  font-size: 20px;
  position: relative;
}

.brand-mark::after {
  content: "";
  position: absolute;
  width: 8px;
  height: 8px;
  background: var(--mint);
  border: 2px solid #fff;
  border-radius: 50%;
  right: -2px;
  bottom: -2px;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.brand-name {
  font-weight: 700;
  font-size: 16.5px;
  letter-spacing: 0.5px;
  color: var(--ink);
}

.brand-tag {
  font-size: 11.5px;
  color: var(--ink-3);
  margin-top: 2px;
}

.side-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px 16px;
  font-weight: 600;
  border-radius: var(--r-md);
  font-size: 14px;
  border: 0;
  cursor: pointer;
  transition: transform .12s ease, box-shadow .15s ease, background .15s;
}
.btn:active { transform: translateY(1px); }
.btn:disabled { opacity: 0.7; cursor: not-allowed; }

.btn-primary {
  background: linear-gradient(135deg, #FF8B6F 0%, #FF6F4E 100%);
  color: #fff;
  box-shadow: var(--sh-coral);
}
.btn-primary:hover { box-shadow: 0 8px 24px -6px rgba(255, 126, 95, .7); }

.upload-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px;
  background: linear-gradient(135deg, #E8F7F0 0%, #DFF1E7 100%);
  border: 1px solid rgba(92, 200, 166, .25);
  border-radius: var(--r-md);
  transition: transform .15s ease, box-shadow .15s ease;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  text-decoration: none;
}
.upload-card:hover {
  transform: translateY(-1px);
  box-shadow: var(--sh-2);
}
.upload-card.active {
  background: linear-gradient(135deg, #C9EFE0 0%, #B4E5D0 100%);
  border-color: rgba(92, 200, 166, .5);
}
.upload-card::after {
  content: "";
  position: absolute;
  width: 60px;
  height: 60px;
  background: rgba(255,255,255,.4);
  border-radius: 50%;
  right: -20px;
  top: -20px;
  filter: blur(8px);
}

.upload-icon {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  background: #fff;
  display: grid;
  place-items: center;
  box-shadow: 0 4px 10px -2px rgba(92, 200, 166, .35);
  flex-shrink: 0;
}

.upload-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.upload-title {
  font-weight: 600;
  font-size: 13.5px;
  color: #2A6B53;
}

.upload-sub {
  font-size: 11px;
  color: #4F8D77;
  margin-top: 2px;
}

.history {
  flex: 1;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  box-shadow: var(--sh-1);
  padding: 8px;
  overflow-y: auto;
  min-height: 0;
}

.history-section {
  padding: 10px 10px 4px;
  font-size: 11px;
  color: var(--ink-3);
  letter-spacing: 1px;
  text-transform: uppercase;
  font-weight: 600;
}

.dialog-group {
  margin-bottom: 8px;
}

.empty-state {
  padding: 28px 18px;
  border-radius: var(--r-md);
  background: rgba(255, 255, 255, 0.8);
  border: 1px dashed var(--border-strong);
  text-align: center;
  color: var(--ink-3);
  margin: 8px;
}

.empty-icon {
  font-size: 28px;
  margin-bottom: 10px;
}

.empty-text {
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--ink-2);
}

.empty-hint {
  font-size: 13px;
  color: var(--ink-3);
}

.content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

@media (max-width: 1100px) {
  .sidebar { width: 240px; }
}

@media (max-width: 900px) {
  .conversation-main {
    flex-direction: column;
    gap: 10px;
    padding: 10px;
  }

  .sidebar {
    width: 100%;
    max-height: 320px;
  }
}
</style>
