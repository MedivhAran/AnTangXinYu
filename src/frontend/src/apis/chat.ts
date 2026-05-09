// 主要负责的是与LLM进行对话的接口，LLM输入需要流式输出
import { fetchEventSource } from '@microsoft/fetch-event-source';

export interface Chat {
  dialogId: string
  userInput: string
  fileUrl?: string
  fileName?: string
  glucoseContext?: {
    currentValueMmolL: number
    trend: string
    measuredAt?: string
    source?: string
  }
}

export interface UploadResponse {
  code: number
  message: string
  data: string
}

export function sendMessage(data: Chat, onmessage: any, onclose: any) {
  const ctrl = new AbortController();

  fetchEventSource('/api/v1/completion', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${localStorage.getItem('token') || ''}`
    },
    body: JSON.stringify({
      dialog_id: data.dialogId,
      user_input: data.userInput,
      ...(data.fileUrl
        ? {
            file_url: data.fileUrl,
            file_name: data.fileName || '',
          }
        : {}),
      ...(data.glucoseContext
        ? {
            glucose_context: {
              current_value_mmol_l: data.glucoseContext.currentValueMmolL,
              trend: data.glucoseContext.trend,
              measured_at: data.glucoseContext.measuredAt,
              source: data.glucoseContext.source || 'manual',
            }
          }
        : {}),
    }),
    signal: ctrl.signal,
    openWhenHidden: true,
    async onopen(response: any) {
      if (response.status !== 200) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
    },
    onmessage(msg: any) {
      try {
        onmessage(msg);
      } catch (error) {
        console.error('处理消息时出错:', error);
      }
    },
    onclose() {
      onclose();
    },
    onerror(err: any) {
      console.error('聊天连接错误:', err);
      ctrl.abort();
      throw err;
    }
  });

  return ctrl;
}

// 文件上传功能
export async function uploadFile(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch('/api/v1/upload', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${localStorage.getItem('token') || ''}`
    },
    body: formData
  });

  if (!response.ok) {
    throw new Error(`上传失败: ${response.statusText}`);
  }

  return await response.json();
}
