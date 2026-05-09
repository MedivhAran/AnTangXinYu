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
        
        // 处理消息对
        for (let i = 0; i < messages.length; i += 2) {
          if (i + 1 >= messages.length) {
            // 如果只剩最后一条消息，单独处理
            const lastMsg = messages[i]
            const chatMsg: ChatMessage = {
              personMessage: { content: '' },
              aiMessage: { content: '' },
              eventInfo: []
            }
            
            if (lastMsg.role === 'user') {
              chatMsg.personMessage.content = lastMsg.content
              if (lastMsg.events && Array.isArray(lastMsg.events)) {
                const attachmentEvent = lastMsg.events.find((event: EventData) => event.type === 'attachment')
                if (attachmentEvent?.data?.file_url) {
                  chatMsg.personMessage.fileUrl = attachmentEvent.data.file_url
                  chatMsg.personMessage.fileName = attachmentEvent.data.file_name || ''
                }
              }
            } else if (lastMsg.role === 'assistant') {
              chatMsg.aiMessage.content = lastMsg.content
              
              // 处理events字段，转换为eventInfo格式
              if (lastMsg.events && Array.isArray(lastMsg.events)) {
                // 使用Map来存储每个title的最终事件状态
                const eventMap = new Map<string, any>();
                
                // 遍历所有事件，按title分组，并过滤掉heartbeat类型的事件
                lastMsg.events.forEach((event: EventData) => {
                  // 跳过heartbeat类型的事件
                  if (event.type === 'heartbeat') return;
                  
                  const eventTitle = event.data?.title || event.type || '事件';
                  const currentStatus = event.data?.status || 'END';
                  
                  // 如果是新事件或者当前事件是END/ERROR状态，则更新Map
                  if (!eventMap.has(eventTitle) || 
                      currentStatus === 'END' || 
                      currentStatus === 'ERROR') {
                    eventMap.set(eventTitle, event);
                  }
                });
                
                // 将Map中的事件转换为eventInfo数组
                chatMsg.eventInfo = Array.from(eventMap.values()).map((event: EventData) => {
                  return {
                    event_type: event.data?.title || event.type || '事件',
                    message: event.data?.message || JSON.stringify(event.data),
                    status: event.data?.status || 'END',
                    show: false,
                    tags: event.data?.tags || [],
                    details: event.data?.details || {}
                  }
                });
              }
            }
            
            chatArr.value.push(chatMsg)
            continue
          }
          
          // 正常处理一对消息
          const userMsg = messages[i].role === 'user' ? messages[i] : messages[i+1]
          const aiMsg = messages[i].role === 'assistant' ? messages[i] : messages[i+1]
          
          const chatMsg: ChatMessage = {
            personMessage: { content: userMsg.role === 'user' ? userMsg.content : '' },
            aiMessage: { content: aiMsg.role === 'assistant' ? aiMsg.content : '' },
            eventInfo: []
          }

          if (userMsg.role === 'user' && userMsg.events && Array.isArray(userMsg.events)) {
            const attachmentEvent = userMsg.events.find((event: EventData) => event.type === 'attachment')
            if (attachmentEvent?.data?.file_url) {
              chatMsg.personMessage.fileUrl = attachmentEvent.data.file_url
              chatMsg.personMessage.fileName = attachmentEvent.data.file_name || ''
            }
          }
          
          // 处理AI消息的events字段，转换为eventInfo格式
          if (aiMsg.role === 'assistant' && aiMsg.events && Array.isArray(aiMsg.events)) {
            // 使用Map来存储每个title的最终事件状态
            const eventMap = new Map<string, any>();
            
            // 遍历所有事件，按title分组，并过滤掉heartbeat类型的事件
            aiMsg.events.forEach((event: EventData) => {
              // 跳过heartbeat类型的事件
              if (event.type === 'heartbeat') return;
              
              const eventTitle = event.data?.title || event.type || '事件';
              const currentStatus = event.data?.status || 'END';
              
              // 如果是新事件或者当前事件是END/ERROR状态，则更新Map
              if (!eventMap.has(eventTitle) || 
                  currentStatus === 'END' || 
                  currentStatus === 'ERROR') {
                eventMap.set(eventTitle, event);
              }
            });
            
            // 将Map中的事件转换为eventInfo数组
            chatMsg.eventInfo = Array.from(eventMap.values()).map((event: EventData) => {
              return {
                event_type: event.data?.title || event.type || '事件',
                message: event.data?.message || JSON.stringify(event.data),
                status: event.data?.status || 'END',
                show: false,
                tags: event.data?.tags || [],
                details: event.data?.details || {}
              }
            });
          }
          
          chatArr.value.push(chatMsg)
        }
        
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

  function notifyDialogListRefresh() {
    dialogListRefreshToken.value += 1
  }
  
  return { 
    chatArr, 
    HistoryChat,
    clear,
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
