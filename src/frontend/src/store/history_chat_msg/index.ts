import { defineStore } from 'pinia';
import { ref } from 'vue'
import { ChatMessage } from '../../type';
import { getHistoryMsgAPI, updateDialogNameAPI } from '../../apis/history';
import { ElMessage } from 'element-plus';

// 定义事件数据接口
interface EventData {
  data?: {
    title?: string;
    message?: string;
    status?: string;
    tags?: string[];
    details?: Record<string, any>;
    file_url?: string;
    file_name?: string;
  };
  type?: string;
  timestamp?: number;
}

const ANTANG_AGENT_NAME = '安糖心语'
const DEFAULT_DIALOG_NAME_PATTERN = /^安糖心语(\s*[·-]\s*.*)?$/
const DIALOG_TITLE_MAX_LENGTH = 16

export const isDefaultAntangDialogName = (dialogName?: string) => {
  const cleanName = (dialogName || '').trim()
  return !cleanName || DEFAULT_DIALOG_NAME_PATTERN.test(cleanName)
}

export const buildDialogTitleFromMessage = (message: string) => {
  const cleanMessage = message
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/[>#*_`~\[\](){}|]/g, '')
    .replace(/\s+/g, ' ')
    .trim()

  const titleSource = cleanMessage || `${ANTANG_AGENT_NAME}会话`
  if (titleSource.length <= DIALOG_TITLE_MAX_LENGTH) {
    return titleSource
  }
  return `${titleSource.slice(0, DIALOG_TITLE_MAX_LENGTH)}...`
}

// 把一条 assistant 消息的 events 去重转换成前端 eventInfo（按 title 取最终状态，过滤 heartbeat）。
function buildEventInfoFromEvents(events: any): any[] {
  if (!events || !Array.isArray(events)) {
    return []
  }
  const eventMap = new Map<string, any>();
  events.forEach((event: EventData) => {
    if (event.type === 'heartbeat') return;
    // 主动提醒标记只用于识别消息类型，不作为工具事件卡片展示
    if ((event.data as any)?.event_type === 'proactive_reminder') return;
    const eventTitle = event.data?.title || event.type || '事件';
    const currentStatus = event.data?.status || 'END';
    if (!eventMap.has(eventTitle) ||
        currentStatus === 'END' ||
        currentStatus === 'ERROR') {
      eventMap.set(eventTitle, event);
    }
  });
  return Array.from(eventMap.values()).map((event: EventData) => ({
    event_type: event.data?.title || event.type || '事件',
    message: event.data?.message || JSON.stringify(event.data),
    status: event.data?.status || 'END',
    show: false,
    tags: event.data?.tags || [],
    details: event.data?.details || {}
  }));
}

// 主动提醒消息：后端在 events 里打了 proactive_reminder 标记，且没有前置用户消息。
function isProactiveReminder(msg: any): boolean {
  if (!msg || !Array.isArray(msg.events)) return false
  return msg.events.some((event: any) => event?.data?.event_type === 'proactive_reminder')
}

// 从用户消息的 events 里抽出附件信息。
function extractUserAttachment(msg: any): { fileUrl?: string; fileName?: string } {
  if (!msg.events || !Array.isArray(msg.events)) {
    return {}
  }
  const attachmentEvent = msg.events.find((event: EventData) => event.type === 'attachment')
  if (attachmentEvent?.data?.file_url) {
    return { fileUrl: attachmentEvent.data.file_url, fileName: attachmentEvent.data.file_name || '' }
  }
  return {}
}

export const useHistoryChatStore = defineStore('history_chat_msg', () => {
  const chatArr = ref<ChatMessage[]>([])
  const dialogId = ref('')
  const name = ref('')
  const logo = ref('')
  const agentType = ref('Agent')
  const dialogListRefreshToken = ref(0)
  const loading = ref(false)
  const error = ref('')

  async function renameDefaultDialogByMessage(id: string, message: string) {
    if (!id || !isDefaultAntangDialogName(name.value)) {
      return
    }

    const nextName = buildDialogTitleFromMessage(message)
    if (!nextName || nextName === name.value) {
      return
    }

    try {
      const response = await updateDialogNameAPI(id, nextName)
      if (response.data.status_code === 200) {
        name.value = nextName
        notifyDialogListRefresh()
      }
    } catch (err) {
      console.warn('更新会话标题失败:', err)
    }
  }

  /**
   * 获取历史聊天记录
   * @param id 对话ID
   */
  async function HistoryChat(id: string) {
    console.log('【HistoryChat】开始获取历史消息，dialog_id:', id)
    chatArr.value = [] // 清空现有消息
    loading.value = true
    error.value = ''

    try {
      const response = await getHistoryMsgAPI(id)
      console.log('【HistoryChat】历史消息API返回:', response.data)

      if (response.data.status_code === 200 && Array.isArray(response.data.data)) {
        const messages = response.data.data

        // 设置会话信息
        if (messages.length > 0 && messages[0].dialog_name) {
          name.value = messages[0].dialog_name || '新对话'
          logo.value = messages[0].logo_url || 'https://via.placeholder.com/40x40/3b82f6/ffffff?text=AI'
        }

        // 旧的默认会话名在打开历史时顺手用首条用户消息更新，不额外调用大模型。
        const firstUserMessage = messages.find(
          (message: any) => message.role === 'user' && message.content?.trim()
        )
        if (firstUserMessage) {
          void renameDefaultDialogByMessage(id, firstUserMessage.content)
        }

        // 单遍分组：一条 user + 紧随其后的 assistant 合成一对；没有前置 user 的 assistant
        // （如到点主动提醒）单独成一条。这样能正确渲染"凭空出现的助手消息"，
        // 也不会因为它打乱后续的用户/助手配对。
        const grouped: ChatMessage[] = []
        let i = 0
        while (i < messages.length) {
          const msg = messages[i]

          if (msg.role === 'user') {
            const attachment = extractUserAttachment(msg)
            const chatMsg: ChatMessage = {
              personMessage: { content: msg.content, ...attachment },
              aiMessage: { content: '' },
              eventInfo: []
            }
            // 紧随其后的 assistant 视为对本条用户消息的回复；但主动提醒永远独立成条，不并进来
            if (i + 1 < messages.length && messages[i + 1].role === 'assistant' && !isProactiveReminder(messages[i + 1])) {
              const aiMsg = messages[i + 1]
              chatMsg.aiMessage.content = aiMsg.content
              chatMsg.eventInfo = buildEventInfoFromEvents(aiMsg.events)
              i += 2
            } else {
              i += 1
            }
            grouped.push(chatMsg)
          } else if (msg.role === 'assistant') {
            // 没有前置用户消息的 assistant（主动提醒），personMessage 留空，并标记类型供前端区分
            grouped.push({
              personMessage: { content: '' },
              aiMessage: { content: msg.content, type: isProactiveReminder(msg) ? 'reminder' : undefined },
              eventInfo: buildEventInfoFromEvents(msg.events)
            })
            i += 1
          } else {
            i += 1
          }
        }

        chatArr.value = grouped
        console.log('【HistoryChat】处理后的消息数组:', chatArr.value)
      } else {
        console.error('【HistoryChat】API返回错误:', response.data)
        error.value = '获取历史消息失败'
        ElMessage.error('获取历史消息失败')
      }
    } catch (err) {
      console.error('【HistoryChat】获取历史消息出错:', err)
      error.value = '获取历史消息出错'
      ElMessage.error('获取历史消息出错，请重试')
    } finally {
      loading.value = false
    }
  }

  /**
   * 清空聊天记录
   */
  function clear() {
    chatArr.value = []
    error.value = ''
  }

  /**
   * 追加一条主动提醒消息（到点心跳推送、且当前正打开该会话时实时插入）。
   */
  function appendProactiveMessage(content: string) {
    chatArr.value.push({
      personMessage: { content: '' },
      aiMessage: { content, type: 'reminder' },
      eventInfo: []
    })
  }

  function notifyDialogListRefresh() {
    dialogListRefreshToken.value += 1
  }

  return {
    chatArr,
    HistoryChat,
    clear,
    appendProactiveMessage,
    dialogId,
    name,
    logo,
    agentType,
    dialogListRefreshToken,
    notifyDialogListRefresh,
    renameDefaultDialogByMessage,
    loading,
    error
  }
},
{
  persist: true
})
