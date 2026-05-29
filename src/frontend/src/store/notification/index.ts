import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ElNotification } from 'element-plus'
import router from '../../router'
import { getNotificationInboxAPI, markNotificationReadAPI } from '../../apis/reminder'
import { useHistoryChatStore } from '../history_chat_msg'

const POLL_INTERVAL_MS = 30000
const ANTANG_AGENT_TYPE = 'AnTangAgent'

// 到点主动提醒的轮询：每 30s 拉一次未读通知，红点 + toast，
// 若正打开对应会话则把消息实时插进聊天流。手机端推送是另一条线，这里只管 web。
export const useNotificationStore = defineStore('notification', () => {
  // 有未读主动提醒的会话集合（驱动会话列表红点）
  const unreadDialogIds = ref<Set<string>>(new Set())

  // dialogId -> 该会话下未读通知的 id 列表，打开会话时据此调用 mark-read
  const unreadIdsByDialog = new Map<string, string[]>()
  let timer: number | null = null
  let visListener: (() => void) | null = null
  // 见过的最大 create_time（服务端 +08:00 ISO 字符串，原样回传保证时区一致），
  // 空串时后端只返回未读，作为页面加载/刷新后的红点基线。
  let lastChecked = ''
  let firstPoll = true

  function addUnread(dialogId: string, notificationId: string) {
    const next = new Set(unreadDialogIds.value)
    next.add(dialogId)
    unreadDialogIds.value = next
    const ids = unreadIdsByDialog.get(dialogId) || []
    ids.push(notificationId)
    unreadIdsByDialog.set(dialogId, ids)
  }

  async function poll() {
    if (!localStorage.getItem('token')) return // 未登录不轮询
    try {
      const res = await getNotificationInboxAPI(lastChecked || undefined)
      const items = res?.data?.data
      if (res?.data?.status_code !== 200 || !Array.isArray(items) || items.length === 0) {
        firstPoll = false
        return
      }

      const historyChat = useHistoryChatStore()
      const openDialogId = historyChat.dialogId
      const readNow: string[] = []

      for (const n of items) {
        if (n.create_time && n.create_time > lastChecked) {
          lastChecked = n.create_time
        }
        if (!n.dialog_id) continue

        if (n.dialog_id === openDialogId) {
          // 正在看这个会话：直接把提醒消息插进去并标记已读
          historyChat.appendProactiveMessage(n.content)
          readNow.push(n.id)
        } else {
          // 其它会话：标红点；非首轮才弹 toast，避免刷新页面时把历史未读一次性弹出来
          addUnread(n.dialog_id, n.id)
          if (!firstPoll) {
            ElNotification({
              title: '安糖心语提醒',
              message: n.content,
              type: 'info',
              duration: 8000,
              onClick: () => {
                router.push({
                  path: '/conversation/chatPage',
                  query: { dialog_id: n.dialog_id, agent_type: ANTANG_AGENT_TYPE },
                })
              },
            })
          }
        }
      }

      if (readNow.length) {
        try { await markNotificationReadAPI(readNow) } catch { /* 静默 */ }
      }
    } catch {
      // 轮询失败静默，不打扰用户，下个周期重试
    } finally {
      firstPoll = false
    }
  }

  function startPolling() {
    if (timer !== null) return
    void poll()
    timer = window.setInterval(poll, POLL_INTERVAL_MS)
    visListener = () => {
      if (document.visibilityState === 'visible') void poll()
    }
    document.addEventListener('visibilitychange', visListener)
  }

  function stopPolling() {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
    if (visListener) {
      document.removeEventListener('visibilitychange', visListener)
      visListener = null
    }
  }

  // 打开某会话时清掉它的红点，并把对应未读通知标记为已读
  async function markDialogRead(dialogId: string) {
    if (!dialogId || !unreadDialogIds.value.has(dialogId)) return
    const next = new Set(unreadDialogIds.value)
    next.delete(dialogId)
    unreadDialogIds.value = next
    const ids = unreadIdsByDialog.get(dialogId) || []
    unreadIdsByDialog.delete(dialogId)
    if (ids.length) {
      try { await markNotificationReadAPI(ids) } catch { /* 静默 */ }
    }
  }

  return { unreadDialogIds, startPolling, stopPolling, markDialogRead }
})
