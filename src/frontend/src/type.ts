export  interface DialogCreateType {
    name: string,
    agent_type: string,
}
// searchType
export  interface searchType {
  name:string,
}

export  interface MsgLikeType {
    userInput:string,
    agentOutput:string,
}

export interface HistoryListType {
  agent: string
  dialogId: string
  name: string
  createTime: string
  lastActiveTime?: string
  logo:string
  agentType?: string
}

export interface MessageType {
  content: string
  type?: string // 新增：支持消息类型
  fileUrl?: string
  fileName?: string
}

export interface ChatMessage {
  personMessage: MessageType
  aiMessage: MessageType
  eventInfo?: Array<{
    event_type: string
    show: boolean
    status: string
    message: string
    tags?: string[]
    details?: Record<string, any>
  }>
}

export interface ToolOption {
  id: string
  name: string
  description: string
  logo_url?: string
  en_name?: string
  zh_name?: string
}

export interface LLMOption {
  id: string
  name: string
  model: string
  provider?: string
  api_key?: string
  base_url?: string
}

export interface MCPOption {
  id: string
  name: string
  description: string
  url?: string
  type?: string
  tools?: string[]
}
