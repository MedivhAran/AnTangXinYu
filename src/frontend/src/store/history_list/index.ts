import { defineStore } from 'pinia';
import { ref } from 'vue'
import { HistoryListType } from "../../type"
import { getDialogListAPI} from '../../apis/history';

export const useHistoryListStore = defineStore('history_list', () => {
  const historyList = ref<HistoryListType[]>([])
  
  async function getList() {
    try {
      const list = await getDialogListAPI()
      historyList.value = (list.data.data || []).map((item: any) => ({
        dialogId: item.dialog_id,
        name: item.name,
        agent: item.agent_name || item.name || '智能助手',
        createTime: item.last_active_time || item.update_time || item.create_time || '',
        lastActiveTime: item.last_active_time || item.update_time || item.create_time || '',
        logo: item.agent_logo_url || '',
        agentType: item.agent_type || 'Agent'
      }))
    } catch (error) {
      console.error('获取会话列表失败:', error)
      historyList.value = []
    }
  }
  
  return { historyList, getList }
},
  {
    persist: true
  }
)
