<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { ElMessage } from "element-plus"

import histortCard from '../../components/historyCard/histortCard.vue'
import { getAgentsAPI, type AgentResponse } from "../../apis/agent"
import { createDialogAPI, deleteDialogAPI, getDialogListAPI } from "../../apis/history"
import type { DialogCreateType, HistoryListType } from "../../type"
import { useHistoryChatStore } from "../../store/history_chat_msg"

const router = useRouter()
const route = useRoute()
const historyChatStore = useHistoryChatStore()

const dialogs = ref<HistoryListType[]>([])
const agents = ref<AgentResponse[]>([])
const loading = ref(false)
const creating = ref(false)
const selectedDialog = ref("")

const ANTANG_AGENT_NAME = "安糖心语"
const ANTANG_AGENT_TYPE = "AnTangAgent"
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

const normalizeAgentId = (agent: AgentResponse) => {
  return (agent as any).id || agent.agent_id
}

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

  router.push({
    path: "/conversation/chatPage",
    query: {
      dialog_id: dialogId,
      agent_type: dialog.agentType || ANTANG_AGENT_TYPE,
    },
  })
}

const fetchAgents = async () => {
  const response = await getAgentsAPI()
  if (response.data.status_code !== 200) {
    throw new Error(response.data.status_message || "获取智能体列表失败")
  }
  agents.value = response.data.data.filter(
    (agent) => agent.name === ANTANG_AGENT_NAME || (agent as any).agent_type === ANTANG_AGENT_TYPE
  )
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

  const agent = agents.value[0]
  if (!agent) {
    ElMessage.error("未找到安糖心语智能体")
    return
  }

  creating.value = true
  try {
    const payload: DialogCreateType = {
      name: buildDialogName(),
      agent_id: normalizeAgentId(agent),
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
    await Promise.all([fetchAgents(), fetchDialogs()])
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

onMounted(initialize)
</script>

<template>
  <div class="conversation-main">
    <aside class="sidebar">
      <div class="brand-card">
        <div class="brand-row">
          <div class="brand-mark">糖</div>
          <div>
            <div class="brand-title">{{ ANTANG_AGENT_NAME }}</div>
            <div class="brand-desc">陪你走过每一天</div>
          </div>
        </div>
      </div>

      <button class="create-btn-native" :disabled="creating" @click="createDialog">
        <span class="create-icon">+</span>
        <span>{{ creating ? "正在创建..." : "新建对话" }}</span>
      </button>

      <div class="dialog-list">
        <div v-if="loading" class="empty-state">
          <div class="empty-icon">...</div>
          <div class="empty-text">正在加载会话...</div>
        </div>

        <div v-else-if="filteredDialogs.length === 0" class="empty-state">
          <div class="empty-icon">糖</div>
          <div class="empty-text">还没有安糖会话</div>
          <div class="empty-hint">点击上方按钮，开始第一轮对话。</div>
        </div>

        <template v-else>
          <section v-for="group in groupedDialogs" :key="group.label" class="dialog-group">
            <div class="dialog-group-title">{{ group.label }}</div>
            <histortCard
              v-for="dialog in group.dialogs"
              :key="dialog.dialogId"
              :item="dialog"
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
  box-sizing: border-box;
  display: flex;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: #f4f2ea;
}

.conversation-main * {
  box-sizing: border-box;
}

.sidebar {
  width: 270px;
  flex-shrink: 0;
  min-height: 0;
  margin: 20px 0 20px 20px;
  padding: 20px;
  border: 1px solid #d8d4c9;
  border-radius: 12px;
  background: #fbfaf6;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.brand-card {
  padding: 0 2px;
  color: #111827;
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: #4f9d73;
  color: #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 800;
}

.brand-title {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 8px;
}

.brand-desc {
  font-size: 14px;
  line-height: 1.6;
  color: #8a8174;
}

.create-btn-native {
  height: 52px;
  border: 1px solid #d8d4c9;
  border-radius: 10px;
  padding: 0 14px;
  background: #f4f2ec;
  color: #111827;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  box-shadow: none;
  transition: background 0.2s ease, border-color 0.2s ease, opacity 0.2s ease;

  &:hover {
    background: #eeece5;
    border-color: #c9c2b6;
  }

  &:disabled {
    opacity: 0.7;
    cursor: not-allowed;
  }
}

.create-icon {
  font-size: 17px;
}

.dialog-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 0;
}

.dialog-group {
  margin-bottom: 22px;
}

.dialog-group-title {
  margin: 0 0 10px 2px;
  font-size: 14px;
  font-weight: 600;
  color: #8a8174;
}

.empty-state {
  padding: 28px 18px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.8);
  border: 1px dashed #cbd5e1;
  text-align: center;
  color: #475569;
}

.empty-icon {
  font-size: 28px;
  margin-bottom: 10px;
}

.empty-text {
  font-weight: 600;
  margin-bottom: 6px;
}

.empty-hint {
  font-size: 13px;
  color: #64748b;
}

.content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
  padding: 20px;
}

@media (max-width: 900px) {
  .conversation-main {
    flex-direction: column;
  }

  .sidebar {
    width: 100%;
    margin: 0;
    border-radius: 0;
    max-height: 280px;
    border-right: none;
    border-bottom: 1px solid #dbe3ee;
  }
}
</style>
