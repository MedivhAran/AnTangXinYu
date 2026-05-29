<script setup lang="ts">
import { ref, onMounted, nextTick, watch, computed } from "vue"
import { useRoute } from 'vue-router'
import { MdPreview } from "md-editor-v3"
import "md-editor-v3/lib/style.css"
import { sendMessage, type Chat } from "../../../apis/chat"
import { isDefaultAntangDialogName, useHistoryChatStore } from "../../../store/history_chat_msg"
import { ElScrollbar, ElInput, ElButton, ElMessage, ElUpload, ElIcon } from "element-plus"
import { UploadFilled, Promotion, Loading, VideoPause, Close } from '@element-plus/icons-vue'

// 使用与ChatMessage接口中定义的eventInfo类型一致的接口
interface EventInfo {
  event_type: string
  show: boolean
  status: string
  message: string
  tags?: string[]
  details?: Record<string, any>
}

const searchInput = ref("")
const sendQuestion = ref(true)
const historyChatStore = useHistoryChatStore()
const scrollbar = ref<InstanceType<typeof ElScrollbar>>()
const route = useRoute()
const abortCtrl = ref<AbortController | null>(null)
const isCancelled = ref(false)
// 标记是否有正在进行的事件
const hasActiveEvents = ref(false)
// 保存上传文件的URL和文件名
const fileUrl = ref("")
const fileName = ref("")
const glucoseCurrentValue = ref("")
const glucoseTrend = ref("unknown")
const glucoseMeasuredAt = ref("")
const showGlucoseEditor = ref(false)

const uploadHeaders = computed(() => ({
  Authorization: `Bearer ${localStorage.getItem('token') || ''}`
}))
const isAnTangConversation = computed(() => {
  const routeAgentType = route.query.agent_type
  if (typeof routeAgentType === 'string' && routeAgentType) {
    return routeAgentType === 'AnTangAgent'
  }
  return historyChatStore.agentType === 'AnTangAgent'
})

// 检查是否有活跃事件
const checkActiveEvents = (chatItem: any) => {
  if (!chatItem.eventInfo || chatItem.eventInfo.length === 0) {
    return false
  }
  return chatItem.eventInfo.some((event: EventInfo) => event.status === 'START')
}

const handleUploadSuccess = (response: any, file: any, fileList: any) => {
  ElMessage.success(`文件 ${file.name} 上传成功!`)
  console.log(response)
  // 保存上传成功返回的文件URL和文件名
  if (response && response.data) {
    fileUrl.value = response.data
    fileName.value = file.name
  }
}

const handleUploadError = (error: any, file: any, fileList: any) => {
  ElMessage.error(`文件 ${file.name} 上传失败.`)
  console.error(error)
}

const buildLocalDatetimeValue = () => {
  const now = new Date()
  const localTime = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
  return localTime.toISOString().slice(0, 16)
}

const syncAgentTypeFromRoute = () => {
  if (typeof route.query.agent_type === 'string' && route.query.agent_type) {
    historyChatStore.agentType = route.query.agent_type
    return
  }
  if (!historyChatStore.agentType) {
    historyChatStore.agentType = 'Agent'
  }
}

const buildGlucoseContext = () => {
  const numericValue = Number(glucoseCurrentValue.value)
  if (!Number.isFinite(numericValue) || numericValue <= 0) {
    return undefined
  }

  return {
    currentValueMmolL: Number(numericValue.toFixed(1)),
    trend: glucoseTrend.value || 'unknown',
    measuredAt: glucoseMeasuredAt.value
      ? new Date(glucoseMeasuredAt.value).toISOString()
      : new Date().toISOString(),
    source: 'manual',
  }
}

// 取消上传的文件
const cancelUploadedFile = () => {
  fileUrl.value = ""
  fileName.value = ""
  ElMessage.info('已取消选择的文件')
}

// 判断是否为图片文件以便展示缩略图
const isImageFile = (name: string) => {
  return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name)
}

const currentConversationTitle = computed(() => {
  const cleanName = (historyChatStore.name || '').trim()
  if (cleanName && !isDefaultAntangDialogName(cleanName)) {
    return cleanName
  }
  return '安糖心语对话'
})

const hasSafetyEvent = computed(() =>
  historyChatStore.chatArr.some((chat) =>
    (chat.eventInfo || []).some((event) => isSafetyEvent(event))
  )
)

const riskMode = computed(() => {
  const numericValue = Number(glucoseCurrentValue.value)
  return (Number.isFinite(numericValue) && numericValue > 0 && numericValue <= 3.9) || hasSafetyEvent.value
})

const modeSubtitle = computed(() => riskMode.value ? '已开启高风险守护模式' : '陪伴模式运行中')

const trendLabelMap: Record<string, string> = {
  unknown: '趋势未填写',
  falling: '下降',
  stable: '平稳',
  rising: '上升',
}

const formatGlucoseTime = (value: string) => {
  if (!value) {
    return ''
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return ''
  }
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

const compactGlucoseSummary = computed(() => {
  const numericValue = Number(glucoseCurrentValue.value)
  const chips = []

  if (Number.isFinite(numericValue) && numericValue > 0) {
    chips.push({
      label: `血糖 ${numericValue.toFixed(1)} mmol/L`,
      danger: numericValue <= 3.9,
    })
  } else {
    chips.push({ label: '血糖未填写', danger: false })
  }

  chips.push({
    label: trendLabelMap[glucoseTrend.value] || '趋势未填写',
    danger: glucoseTrend.value === 'falling',
  })

  const measuredTime = formatGlucoseTime(glucoseMeasuredAt.value)
  if (measuredTime) {
    chips.push({ label: measuredTime, danger: false })
  }

  return chips
})

const isSafetyEvent = (event: EventInfo) => {
  const identityText = `${event.event_type || ''} ${(event.tags || []).join(' ')}`
  return /安全判断|安全提醒|safety_assessment|safety_notice|安全优先/i.test(identityText)
}

const safetyEvents = (events?: EventInfo[]) => (events || []).filter(isSafetyEvent)

const timelineEvents = (events?: EventInfo[]) => (events || []).filter((event) => !isSafetyEvent(event))

const eventStatusText = (event: EventInfo) => {
  if (event.status === 'START') {
    return '正在整理回复...'
  }
  if (event.status === 'ERROR') {
    return '处理遇到问题'
  }
  return event.event_type
}

const eventDetailText = (event: EventInfo) => {
  if (event.status === 'START') {
    return event.message || '正在处理中'
  }
  if (event.status === 'ERROR') {
    return event.message || '处理出错'
  }
  return '已完成'
}

const safetyTitle = (event: EventInfo) => {
  if (event.event_type === 'safety_assessment' || event.event_type === '安全判断') {
    return '识别到风险信号'
  }
  if (event.event_type === 'safety_notice' || event.event_type === '安全提醒') {
    return '请立即做'
  }
  return event.event_type || '安全提醒'
}

const isAssistantThinking = (index: number) => !sendQuestion.value && index === historyChatStore.chatArr.length - 1

// 助手卡片右上角状态：到点主动提醒单独标注，且不会被误显示成"正在回应"
const assistantStatusText = (item: any, index: number) => {
  if (item?.aiMessage?.type === 'reminder') return '主动提醒'
  return isAssistantThinking(index) ? '正在回应' : '已回应'
}


// Function to scroll to the bottom of the chat
function scrollBottom() {
  nextTick(() => {
    scrollbar.value?.wrapRef?.scrollTo(0, scrollbar.value?.wrapRef.scrollHeight)
  })
}

// 处理事件状态更新
const handleEventStatus = (parsedData: any) => {
  const { data } = parsedData
  
  // 确保事件有title字段，如果没有则使用event_type或默认值
  const eventId = data.title || data.event_type || "event"
  const { status, message } = data
  
  // 获取最后一条AI消息
  const lastChat = historyChatStore.chatArr[historyChatStore.chatArr.length - 1]
  
  // 初始化eventInfo数组（如果不存在）
  if (!lastChat.eventInfo) {
    lastChat.eventInfo = []
  }
  
  // 查找是否已有相同事件类型的事件
  const existingEventIndex = lastChat.eventInfo.findIndex(
    (event) => event.event_type === eventId
  )
  
  if (status === 'START') {
    // 如果是新事件，添加到事件列表
    if (existingEventIndex === -1) {
      lastChat.eventInfo.push({
        event_type: eventId,
        message: message || "处理中...",
        status: status,
        show: false,
        tags: data.tags || [],
        details: data.details || {}
      })
    } else {
      // 更新已有事件
      lastChat.eventInfo[existingEventIndex].status = status
      lastChat.eventInfo[existingEventIndex].message = message || "处理中..."
      lastChat.eventInfo[existingEventIndex].tags = data.tags || []
      lastChat.eventInfo[existingEventIndex].details = data.details || {}
    }
    // 设置有活跃事件
    hasActiveEvents.value = true
  } else if (status === 'END' || status === 'ERROR') {
    // 更新已有事件状态
    if (existingEventIndex !== -1) {
      lastChat.eventInfo[existingEventIndex].status = status
      if (message) {
        lastChat.eventInfo[existingEventIndex].message = message
      }
    } else {
      // 如果没有找到对应的事件，创建一个新事件
      lastChat.eventInfo.push({
        event_type: eventId,
        message: message || (status === 'END' ? "已完成" : "处理出错"),
        status: status,
        show: false,
        tags: data.tags || [],
        details: data.details || {}
      })
    }
    
    // 检查是否还有其他活跃事件
    hasActiveEvents.value = checkActiveEvents(lastChat)
  }
  
  scrollBottom()
}

// Function to handle sending a message
const personQuestion = async () => {
  if (!historyChatStore.dialogId) {
    ElMessage.error('未获取到会话 ID，请先选择或创建会话')
    return
  }
  if ((searchInput.value.trim() || fileUrl.value) && sendQuestion.value) {
    sendQuestion.value = false
    isCancelled.value = false
    hasActiveEvents.value = false
    const currentInput = searchInput.value.trim() || '请结合这张图和我当前情况给我建议。'
    const shouldRenameDialog =
      historyChatStore.chatArr.length === 0 && isDefaultAntangDialogName(historyChatStore.name)
    searchInput.value = ""

    historyChatStore.chatArr.push({
      personMessage: {
        content: currentInput,
        fileUrl: fileUrl.value || undefined,
        fileName: fileName.value || undefined,
      },
      aiMessage: { content: "" }, // 设置初始空内容，后续会被chunks累加
      eventInfo: [] // 初始化事件信息数组
    })
    if (shouldRenameDialog) {
      void historyChatStore.renameDefaultDialogByMessage(historyChatStore.dialogId, currentInput)
    }
    scrollBottom()

    const data: Chat = {
      dialogId: historyChatStore.dialogId,
      userInput: currentInput,
    }
    
    // 如果有上传的文件URL，添加到请求中，然后立刻清空输入区附件状态。
    // 文件 URL 已经同时写进了 personMessage（聊天记录）和 data（请求体），
    // 输入区不再需要保留 —— 清空时机绑在 onclose 异步回调上不可靠。
    if (fileUrl.value) {
      data.fileUrl = fileUrl.value
      data.fileName = fileName.value
      fileUrl.value = ""
      fileName.value = ""
    }
    if (isAnTangConversation.value) {
      const glucoseContext = buildGlucoseContext()
      if (glucoseContext) {
        data.glucoseContext = glucoseContext
      }
    }

    try {
      abortCtrl.value = sendMessage(
        data,
        (msg: any) => {
          if (isCancelled.value) {
            historyChatStore.chatArr[historyChatStore.chatArr.length - 1].aiMessage.content = '已取消本次对话！'
            return
          }
          try {
            const parsedData = JSON.parse(msg.data)
            // 移除这些可能含有敏感信息的日志
            // console.log("---------------------------")
            // console.log(parsedData.data)
            
            if (parsedData.data.tools && Array.isArray(parsedData.data.tools)) {
              // data.value.tools = parsedData.data.tools // This line was removed from the original file
            }
            // 只有明确带 content 的非流式消息才覆盖主回复，避免工具事件清空已有内容。
            if (parsedData.type !== 'response_chunk' && parsedData.data?.content) {
              historyChatStore.chatArr[historyChatStore.chatArr.length - 1].aiMessage.content = parsedData.data.content || ''
            }
            if (parsedData.data.session_id) {
              // sessionId.value = parsedData.data.session_id // This line was removed from the original file
              // sessionStore().updateSessionId(sessionId.value) // This line was removed from the original file
            }
            // 处理不同类型的消息
            if (parsedData.type === 'response_chunk') {
              // 累加chunk内容而不是替换
              const lastMessage = historyChatStore.chatArr[historyChatStore.chatArr.length - 1]
              if (!lastMessage.aiMessage.content) {
                lastMessage.aiMessage.content = parsedData.data.chunk
              } else {
                lastMessage.aiMessage.content += parsedData.data.chunk
              }
              scrollBottom()
              // console.log('【Chunk接收】当前累加内容:', lastMessage.aiMessage.content) // 调试用
            } else if (parsedData.type === 'event') {
              // 处理事件消息
              handleEventStatus(parsedData)
            } else if (parsedData.type === 'error') {
              historyChatStore.chatArr.push({
                personMessage: { content: '' },
                aiMessage: { content: '[错误]\n' + (parsedData.data.message || ''), type: 'error' },
                eventInfo: []
              })
              scrollBottom()
            } else if (parsedData.type === 'heartbeat') {
              // 心跳包可忽略
            } else {
              // 其他类型作为普通消息展示
              historyChatStore.chatArr.push({
                personMessage: { content: '' },
                aiMessage: { content: '[系统消息]\n' + JSON.stringify(parsedData.data), type: 'system' },
                eventInfo: []
              })
              scrollBottom()
            }
          } catch (error) {
            console.error('解析消息失败:', error)
          }
        },
        () => {
          sendQuestion.value = true
          abortCtrl.value = null
          hasActiveEvents.value = false
          // 清空文件URL和文件名
          fileUrl.value = ""
          fileName.value = ""
          historyChatStore.notifyDialogListRefresh()
        }
      )
    } catch (error) {
      ElMessage.error('发送消息失败，请重试')
      sendQuestion.value = true
      abortCtrl.value = null
      hasActiveEvents.value = false
      // 清空文件URL和文件名
      fileUrl.value = ""
      fileName.value = ""
    }
  }
}

const stopGeneration = () => {
  if (abortCtrl.value) {
    // console.log('[stopGeneration] 用户点击暂停, abort 请求')
    isCancelled.value = true
    abortCtrl.value.abort()
    const lastMessage = historyChatStore.chatArr[historyChatStore.chatArr.length - 1]
    if (lastMessage) {
      //lastMessage.aiMessage.content = '已取消本次AI生成！'
      sendQuestion.value = true
      abortCtrl.value = null
      hasActiveEvents.value = false
      ElMessage.info('已取消本次AI生成！')
    }
  }
}

// 切换事件信息的展开/折叠状态
const toggleEventInfo = (event: EventInfo) => {
  event.show = !event.show
}

// Load history on mount
onMounted(() => {
  syncAgentTypeFromRoute()
  if (!glucoseMeasuredAt.value) {
    glucoseMeasuredAt.value = buildLocalDatetimeValue()
  }

  const dialog_id = route.query.dialog_id
  const message = route.query.message
  
  if (dialog_id) {
    historyChatStore.dialogId = dialog_id as string
    historyChatStore.HistoryChat(dialog_id as string).then(() => {
        scrollBottom()
        
        // 如果有来自首页的搜索消息，自动发送
        if (message && typeof message === 'string') {
          searchInput.value = message
          nextTick(() => {
            personQuestion()
          })
        }
    })
  } else if (message && typeof message === 'string') {
    // 新会话，直接发送首页的搜索消息
    searchInput.value = message
    nextTick(() => {
      personQuestion()
    })
  }
})

// Watch for route changes to load new chat history
watch(
  () => route.query.agent_type,
  () => {
    syncAgentTypeFromRoute()
  }
)

watch(
  () => route.query.dialog_id,
  (newVal, oldVal) => {
    if (newVal && newVal !== oldVal) {
      syncAgentTypeFromRoute()
      historyChatStore.dialogId = newVal as string
      historyChatStore.HistoryChat(newVal as string).then(() => {
        scrollBottom()
        
        // 如果有来自首页的搜索消息，自动发送
        const message = route.query.message
        if (message && typeof message === 'string') {
          searchInput.value = message
          nextTick(() => {
            personQuestion()
          })
        }
      })
    }
  }
)

// Watch for new messages to scroll down
watch(
  () => historyChatStore.chatArr,
  (newVal) => {
    // console.log('【消息更新】历史消息数组更新:', JSON.stringify(newVal))
    scrollBottom()
  },
  { deep: true }
)

watch(
  () => isAnTangConversation.value,
  (isAnTang) => {
    if (isAnTang) {
      if (!glucoseMeasuredAt.value) {
        glucoseMeasuredAt.value = buildLocalDatetimeValue()
      }
    }
  },
  { immediate: true }
)
</script>

<template>
  <div class="chat-container">
    <header class="chat-header">
      <div>
        <h1>{{ currentConversationTitle }}</h1>
        <p>{{ modeSubtitle }}</p>
      </div>
      <button class="more-btn" aria-label="更多操作">...</button>
    </header>

    <div class="chat-conversation">
      <el-scrollbar ref="scrollbar" class="message-scrollbar">
        <!-- 聊天消息区 -->
        <div v-for="(item, index) in historyChatStore.chatArr" :key="index" class="message-group">
          <!-- User Message -->
          <div v-if="item.personMessage.content" class="user-message">
            <div class="message-content">
              <span>{{ item.personMessage.content }}</span>
              <div v-if="item.personMessage.fileUrl" class="user-attachment">
                <img
                  v-if="isImageFile(item.personMessage.fileName || '')"
                  :src="item.personMessage.fileUrl"
                  alt="用户上传图片"
                  class="user-attachment-image"
                />
                <a
                  v-else
                  class="user-attachment-file"
                  :href="item.personMessage.fileUrl"
                  target="_blank"
                  rel="noopener"
                >
                  {{ item.personMessage.fileName || '查看附件' }}
                </a>
              </div>
            </div>
          </div>
          
          <!-- AI Message -->
          <div v-if="item.aiMessage.content || (!sendQuestion && index === historyChatStore.chatArr.length - 1)" class="ai-message" :class="item.aiMessage.type ? 'ai-message-' + item.aiMessage.type : ''">
            <div class="msg-avatar bot">糖</div>
            <div class="assistant-card">
              <div class="assistant-head">
                <span class="assistant-name">安糖</span>
                <span class="assistant-status">{{ assistantStatusText(item, index) }}</span>
              </div>

              <!-- 事件进度信息，按参考图展示为纵向进度线 -->
              <div v-if="timelineEvents(item.eventInfo).length" class="event-timeline">
                <div v-for="(event, evIdx) in timelineEvents(item.eventInfo)" :key="evIdx" class="event-step" :class="event.status">
                  <span class="event-dot"></span>
                  <div class="event-step-content" @click="toggleEventInfo(event)">
                    <div class="event-step-title">{{ eventStatusText(event) }}</div>
                    <div class="event-step-desc">{{ eventDetailText(event) }}</div>
                    <div v-if="event.show" class="event-info-message">
                      {{ event.message }}
                      <div v-if="event.tags && event.tags.length" class="event-tags">
                        <span v-for="tag in event.tags" :key="tag" class="event-tag">{{ tag }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div v-if="safetyEvents(item.eventInfo).length" class="safety-list">
                <div v-for="(event, evIdx) in safetyEvents(item.eventInfo)" :key="evIdx" class="safety-card">
                  <div class="safety-title">{{ safetyTitle(event) }}</div>
                  <div class="safety-text">{{ event.message }}</div>
                </div>
              </div>
              
              <!-- Loading Indicator - 只在没有活跃事件时显示 -->
              <div v-if="!item.aiMessage.content && !sendQuestion && index === historyChatStore.chatArr.length - 1 && !hasActiveEvents" class="loading-spinner">
                <el-icon class="is-loading" :size="20"><Loading /></el-icon>
              </div>
              <template v-else>
                <div class="assistant-content">
                  <div v-if="item.aiMessage.type === 'event'" style="color: #67c23a;">
                    <MdPreview :editorId="'ai-event-' + index" :modelValue="item.aiMessage.content" />
                  </div>
                  <div v-else-if="item.aiMessage.type === 'error'" style="color: #f56c6c;">
                    <MdPreview :editorId="'ai-error-' + index" :modelValue="item.aiMessage.content" />
                  </div>
                  <div v-else-if="item.aiMessage.type === 'system'" style="color: #e6a23c;">
                    <MdPreview :editorId="'ai-system-' + index" :modelValue="item.aiMessage.content" />
                  </div>
                  <MdPreview v-else :editorId="'ai-' + index" :modelValue="item.aiMessage.content" />
                </div>
              </template>
            </div>
          </div>
        </div>
      </el-scrollbar>
    </div>

    <div class="input-area">
      <div class="input-bar">
        <el-upload
          action="/api/v1/upload"
          :on-success="handleUploadSuccess"
          :on-error="handleUploadError"
          :show-file-list="false"
          :disabled="!!fileUrl"
          :headers="uploadHeaders"
        >
          <el-button circle class="action-btn" :class="{ 'file-uploaded': fileUrl }">
            <el-icon><UploadFilled /></el-icon>
          </el-button>
        </el-upload>
        <div class="input-wrapper">
          <div v-if="isAnTangConversation" class="glucose-summary">
            <span class="glucose-summary-label">当前状态</span>
            <span
              v-for="chip in compactGlucoseSummary"
              :key="chip.label"
              class="glucose-chip"
              :class="{ danger: chip.danger }"
            >
              {{ chip.label }}
            </span>
            <button class="glucose-edit" type="button" @click="showGlucoseEditor = !showGlucoseEditor">
              {{ showGlucoseEditor ? '收起' : '编辑' }}
            </button>
          </div>
          <div v-if="isAnTangConversation && showGlucoseEditor" class="glucose-panel">
            <div class="glucose-panel-title">当前血糖信息（可选）</div>
            <div class="glucose-grid">
              <label class="glucose-field">
                <span>血糖值 mmol/L</span>
                <input
                  v-model="glucoseCurrentValue"
                  type="number"
                  min="0"
                  max="40"
                  step="0.1"
                  placeholder="如 3.8"
                />
              </label>
              <label class="glucose-field">
                <span>趋势</span>
                <select v-model="glucoseTrend">
                  <option value="unknown">未填写</option>
                  <option value="falling">下降</option>
                  <option value="stable">平稳</option>
                  <option value="rising">上升</option>
                </select>
              </label>
              <label class="glucose-field glucose-field-time">
                <span>测量时间</span>
                <input
                  v-model="glucoseMeasuredAt"
                  type="datetime-local"
                />
              </label>
            </div>
          </div>
          <!-- 已上传文件显示 -->
          <div v-if="fileUrl" class="uploaded-file-tag">
            <span class="file-avatar" aria-hidden="true">
              <img v-if="isImageFile(fileName)" :src="fileUrl" alt="" />
              <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M21.44 11.05l-7.07 7.07a5 5 0 01-7.07-7.07l7.07-7.07a3 3 0 114.24 4.24l-7.07 7.07a1 1 0 01-1.41-1.41l6.36-6.36" stroke="#409eff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </span>
            <a class="file-name" :href="fileUrl" target="_blank" rel="noopener" :title="fileName">{{ fileName }}</a>
            <el-button size="small" type="danger" text @click="cancelUploadedFile" class="cancel-btn" title="移除">
              <el-icon><Close /></el-icon>
            </el-button>
          </div>
          <el-input
            v-model="searchInput"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 3 }"
            :placeholder="isAnTangConversation ? '可以直接说症状、上传餐食图/血糖截图，或描述你现在最担心的事...' : '请输入您的问题...'"
            @keydown.enter.exact.prevent="personQuestion"
            class="message-input"
          />
        </div>
        <el-button
          @click="sendQuestion ? personQuestion() : stopGeneration()"
          type="primary"
          circle
          class="send-btn"
          :class="{ 'pause-mode': !sendQuestion }"
          :disabled="sendQuestion ? (!searchInput.trim() && !fileUrl) : false"
        >
          <el-icon v-if="sendQuestion"><Promotion /></el-icon>
          <el-icon v-else><VideoPause /></el-icon>
        </el-button>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.chat-container {
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

.chat-container * {
  box-sizing: border-box;
}

.chat-header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 26px;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(180deg, #FFFAF3 0%, #FFFFFF 100%);

  > div {
    flex: 1;
    min-width: 0;
  }

  h1 {
    margin: 0;
    font-size: 17px;
    font-weight: 700;
    color: var(--ink);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
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

    &::before {
      content: "";
      width: 6px;
      height: 6px;
      background: var(--mint);
      border-radius: 50%;
      box-shadow: 0 0 0 3px rgba(92, 200, 166, .25);
      animation: pulse 2s infinite;
    }
  }
}

@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 3px rgba(92, 200, 166, .25); }
  50% { box-shadow: 0 0 0 6px rgba(92, 200, 166, 0); }
}

.more-btn {
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: var(--ink-2);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  transition: background .12s;

  &:hover { background: rgba(74, 47, 25, .06); }
}

.chat-conversation {
  flex: 1;
  min-height: 0;
  padding: 0;
  overflow: hidden;
  background:
    radial-gradient(circle at 100% 0%, rgba(255,176,130,.08) 0%, transparent 40%),
    radial-gradient(circle at 0% 100%, rgba(92,200,166,.06) 0%, transparent 40%);

  .message-scrollbar {
    height: 100%;
  }

  .message-group {
    max-width: 760px;
    margin: 0 auto;
    padding: 0 32px;
  }

  .ai-message {
    display: flex;
    gap: 12px;
    margin: 14px 0 22px;
  }

  .user-message {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    margin: 18px 0 12px;

    .message-content {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      background: linear-gradient(135deg, #FF8B6F 0%, #FF6F4E 100%);
      color: #ffffff;
      border: none;
      border-radius: 22px 6px 22px 22px;
      padding: 14px 18px;
      max-width: min(620px, 82%);
      box-shadow: var(--sh-coral);
      word-break: break-word;
      font-size: 15px;
      line-height: 1.6;
      font-weight: 500;
    }
  }
}

.msg-avatar {
  width: 34px;
  height: 34px;
  border-radius: 12px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 14px;
  margin-top: 2px;

  &.bot {
    background: linear-gradient(135deg, #FF8E70 0%, #FFB07A 100%);
    color: #fff;
    box-shadow: 0 4px 12px -3px rgba(255, 126, 95, .45);
  }
}

.assistant-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 6px 22px 22px 22px;
  padding: 18px 22px;
  box-shadow: var(--sh-1);
  flex: 1;
  min-width: 0;
}

.assistant-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.assistant-name {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--ink);
  font-size: 16px;
  font-weight: 700;

  &::before {
    content: "";
    width: 4px;
    height: 16px;
    background: linear-gradient(180deg, var(--coral) 0%, var(--apricot) 100%);
    border-radius: 999px;
  }
}

.assistant-status {
  padding: 2px 10px;
  border-radius: var(--r-pill);
  background: var(--mint-tint);
  color: var(--good-text);
  font-size: 11.5px;
  font-weight: 600;
}

.assistant-content {
  margin-top: 10px;
  color: var(--ink-2);
  line-height: 1.75;
}

.user-attachment {
  margin-top: 10px;
}

.user-attachment-image {
  max-width: 220px;
  border-radius: 12px;
  border: 1px solid rgba(15, 23, 42, 0.12);
  display: block;
}

.user-attachment-file {
  display: inline-flex;
  align-items: center;
  padding: 8px 12px;
  border-radius: 10px;
  background: #ffffff;
  border: 1px solid #dbe3ee;
  color: #2563eb;
  text-decoration: none;
}

.event-timeline {
  position: relative;
  margin: 4px 0 16px;
  padding-left: 8px;
}

.event-step {
  position: relative;
  display: grid;
  grid-template-columns: 22px 1fr;
  gap: 6px;
  padding-bottom: 16px;

  &::before {
    content: '';
    position: absolute;
    left: 7px;
    top: 15px;
    bottom: -2px;
    width: 1px;
    background: var(--border-strong);
  }

  &:last-child::before {
    display: none;
  }

  &.ERROR .event-dot {
    border-color: var(--coral-deep);
    background: var(--coral-deep);
  }
}

.event-dot {
  width: 14px;
  height: 14px;
  margin-top: 4px;
  border-radius: 999px;
  border: 2px solid var(--coral);
  background: var(--coral);
  z-index: 1;
}

.event-step.START .event-dot {
  background: #ffffff;
}

.event-step-content {
  cursor: pointer;
}

.event-step-title {
  color: var(--ink);
  font-size: 14.5px;
  font-weight: 700;
  line-height: 1.4;
}

.event-step-desc {
  margin-top: 2px;
  color: var(--ink-3);
  font-size: 13px;
  line-height: 1.5;
}

.event-info-message {
  margin-top: 8px;
  color: var(--ink-2);
  padding: 8px 10px;
  background: var(--card-tint);
  border-radius: var(--r-sm);
  font-size: 13px;
  line-height: 1.5;
}

.safety-list {
  display: grid;
  gap: 10px;
  margin: 12px 0 16px;
}

.safety-card {
  padding: 14px 18px;
  border-radius: var(--r-md);
  background: var(--risk-bg);
  color: var(--risk-text);
}

.safety-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-weight: 700;
}

.safety-title::before {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--risk);
}

.safety-text {
  font-size: 14px;
  line-height: 1.65;
}

.event-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.event-tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: var(--r-pill);
  background: var(--coral-tint);
  color: var(--coral-deep);
  font-size: 11.5px;
  font-weight: 600;
}

.loading-spinner {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 28px;
  color: var(--coral);
}

.input-area {
  flex-shrink: 0;
  padding: 14px 28px 20px;
  border-top: 1px solid var(--border);
  background: linear-gradient(180deg, rgba(251,246,239,0) 0%, rgba(251,246,239,.8) 100%);

  .input-bar {
    width: min(760px, 100%);
    margin: 0 auto;
    display: flex;
    align-items: flex-end;
    gap: 8px;
    padding: 10px 14px;
    border: 1.5px solid var(--border-strong);
    border-radius: var(--r-lg);
    background: var(--card);
    box-shadow: var(--sh-2);
    transition: border-color .15s, box-shadow .15s;

    &:focus-within {
      border-color: var(--coral);
      box-shadow: 0 0 0 4px rgba(255, 126, 95, .12), var(--sh-2);
    }
  }

  .action-btn {
    background-color: transparent;
    border: none;
    width: 38px;
    height: 38px;
    font-size: 18px;
    color: var(--ink-2);
    border-radius: 12px;
    transition: background .12s, color .12s;
    &:hover {
      background-color: var(--coral-tint);
      color: var(--coral-deep);
    }
    &.file-uploaded {
      background-color: var(--mint-tint);
      color: var(--good-text);
      &:hover {
        background-color: var(--mint-soft);
      }
    }
    &:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }
  }

  .input-wrapper {
    flex: 1;
    min-width: 0;
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 4px 0;
  }

  .glucose-summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
    font-size: 12px;
  }

  .glucose-summary-label {
    color: var(--ink-3);
    font-weight: 600;
    margin-right: 2px;
  }

  .glucose-chip {
    display: inline-flex;
    align-items: center;
    padding: 2px 10px;
    border-radius: var(--r-pill);
    background: rgba(74, 47, 25, .06);
    color: var(--ink-3);
    font-size: 12px;
    font-weight: 500;

    &.danger {
      background: var(--coral-tint);
      color: var(--coral-deep);
    }
  }

  .glucose-edit {
    margin-left: auto;
    border: none;
    background: transparent;
    color: var(--coral-deep);
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }

  .glucose-panel {
    padding: 10px 12px;
    border-radius: var(--r-sm);
    border: 1px solid var(--border);
    background: var(--card-tint);
  }

  .glucose-panel-title {
    margin-bottom: 8px;
    font-size: 12px;
    font-weight: 600;
    color: var(--good-text);
  }

  .glucose-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .glucose-field {
    display: flex;
    flex-direction: column;
    gap: 6px;

    span {
      font-size: 11px;
      color: var(--ink-3);
    }

    input,
    select {
      height: 32px;
      border-radius: 10px;
      border: 1px solid var(--border-strong);
      background: var(--card);
      padding: 0 10px;
      color: var(--ink);
      outline: none;
      font-family: inherit;

      &:focus {
        border-color: var(--coral);
      }
    }
  }

  .glucose-field-time {
    min-width: 0;
  }

  .uploaded-file-tag {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    background: var(--coral-tint);
    border: 1px solid var(--coral-soft);
    border-radius: var(--r-pill);
    font-size: 12px;
    width: fit-content;

    .file-avatar {
      width: 22px;
      height: 22px;
      border-radius: 6px;
      overflow: hidden;
      background: rgba(255, 126, 95, 0.12);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
      }
    }

    .file-name {
      color: var(--coral-deep);
      text-decoration: none;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 180px;
      font-weight: 600;
    }

    .cancel-btn {
      padding: 0;
      width: 20px;
      height: 20px;
      min-height: 20px;
      font-size: 12px;
      margin-left: 4px;

      &:hover {
        background-color: rgba(225, 96, 90, 0.1);
      }
      :deep(.el-icon) {
        font-size: 16px;
      }
    }
  }

  .message-input {
    width: 100%;
    :deep(.el-textarea__inner) {
      min-height: 32px !important;
      border-radius: 0;
      background-color: transparent;
      box-shadow: none;
      border: 0;
      padding: 6px 0;
      font-size: 14.5px;
      line-height: 1.6;
      color: var(--ink);
      resize: none;
      &::placeholder { color: var(--ink-4); }
      &:focus { border: 0; }
    }
  }

  .send-btn {
    background: linear-gradient(135deg, #FF8B6F 0%, #FF6F4E 100%);
    border: none;
    width: 40px;
    height: 40px;
    font-size: 18px;
    color: #fff;
    box-shadow: var(--sh-coral);
    transition: transform .12s, box-shadow .15s;

    &:hover {
      transform: translateY(-1px);
      box-shadow: 0 10px 24px -6px rgba(255, 126, 95, .7);
    }
    &:active { transform: translateY(0); }
    &.pause-mode {
      background: linear-gradient(135deg, #E1605A 0%, #C8483F 100%);
      box-shadow: 0 6px 20px -6px rgba(225, 96, 90, 0.55);
    }
  }
}

@media (max-width: 900px) {
  .input-area {
    padding: 10px 12px 12px;

    .glucose-grid {
      grid-template-columns: 1fr;
    }
  }
}


// Override MdPreview background
:deep(.md-editor-preview-wrapper) {
    background-color: transparent !important;
}

:deep(.md-editor-preview img) {
  max-width: 100%;
  max-height: 420px;
  object-fit: contain;
  border-radius: 10px;
}

:deep(.el-scrollbar__view) {
  padding: 10px;
}

:deep(.el-scrollbar__wrap) {
  overflow-x: hidden;
}
</style>
