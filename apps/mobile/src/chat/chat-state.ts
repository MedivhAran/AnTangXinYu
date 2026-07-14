import type {
  AgentActivityPhase,
  ChatMessage,
  ChatStreamEvent,
} from '../api/types';

export type ChatState = {
  messages: ChatMessage[];
  nextBefore: string | null;
  historyLoading: boolean;
  sending: boolean;
  activity: AgentActivityPhase | null;
  error: string | null;
  pendingClientMessageId: string | null;
};

export const initialChatState: ChatState = {
  messages: [],
  nextBefore: null,
  historyLoading: false,
  sending: false,
  activity: null,
  error: null,
  pendingClientMessageId: null,
};

type ChatAction =
  | { type: 'history-loading' }
  | { type: 'history-replaced'; messages: ChatMessage[]; nextBefore: string | null }
  | { type: 'history-prepended'; messages: ChatMessage[]; nextBefore: string | null }
  | { type: 'history-failed'; message: string }
  | { type: 'send-started'; clientMessageId: string; content: string; now: string }
  | { type: 'stream-event'; event: ChatStreamEvent }
  | { type: 'send-rejected'; clientMessageId: string; message: string | null }
  | { type: 'send-cancelled' }
  | { type: 'transport-failed'; message: string }
  | { type: 'clear-error' };

function hasGeneratingAssistant(messages: ChatMessage[]): boolean {
  return messages.some(
    (message) => message.role === 'assistant' && message.status === 'generating',
  );
}

function updatePendingAssistant(
  state: ChatState,
  update: (message: ChatMessage) => ChatMessage,
): ChatMessage[] {
  const clientId = state.pendingClientMessageId;
  if (clientId === null) throw new Error('收到流事件时没有待处理消息');

  const localAssistantId = `local-assistant-${clientId}`;
  let found = false;
  const messages = state.messages.map((message) => {
    if (message.id !== localAssistantId && message.status !== 'generating') return message;
    if (message.role !== 'assistant') return message;
    found = true;
    return update(message);
  });
  if (!found) throw new Error('找不到正在生成的 AI 消息');
  return messages;
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'history-loading':
      return { ...state, historyLoading: true, error: null };

    case 'history-replaced':
      return {
        ...state,
        messages: action.messages,
        nextBefore: action.nextBefore,
        historyLoading: false,
        sending: hasGeneratingAssistant(action.messages),
        activity: null,
        pendingClientMessageId: null,
        error: null,
      };

    case 'history-prepended': {
      const knownIds = new Set(state.messages.map((message) => message.id));
      const older = action.messages.filter((message) => !knownIds.has(message.id));
      return {
        ...state,
        messages: [...older, ...state.messages],
        nextBefore: action.nextBefore,
        historyLoading: false,
        error: null,
      };
    }

    case 'history-failed':
      return { ...state, historyLoading: false, error: action.message };

    case 'send-started': {
      if (state.sending) throw new Error('已有消息正在发送');
      const userMessage: ChatMessage = {
        id: `local-user-${action.clientMessageId}`,
        clientMessageId: action.clientMessageId,
        role: 'user',
        status: 'completed',
        content: action.content,
        createdAt: action.now,
        completedAt: null,
      };
      const assistantMessage: ChatMessage = {
        id: `local-assistant-${action.clientMessageId}`,
        clientMessageId: null,
        role: 'assistant',
        status: 'generating',
        content: '',
        createdAt: action.now,
        completedAt: null,
      };
      return {
        ...state,
        messages: [...state.messages, userMessage, assistantMessage],
        sending: true,
        activity: 'thinking',
        pendingClientMessageId: action.clientMessageId,
        error: null,
      };
    }

    case 'stream-event': {
      const event = action.event;

      if (event.type === 'message_started') {
        const clientId = state.pendingClientMessageId;
        if (clientId === null) throw new Error('message_started 没有对应的本地消息');
        return {
          ...state,
          messages: state.messages.map((message) => {
            if (message.id === `local-user-${clientId}`) {
              return { ...message, id: event.userMessageId };
            }
            if (message.id === `local-assistant-${clientId}`) {
              return { ...message, id: event.assistantMessageId };
            }
            return message;
          }),
        };
      }

      if (event.type === 'text_delta') {
        return {
          ...state,
          activity: null,
          messages: updatePendingAssistant(state, (message) => ({
            ...message,
            content: message.content + event.delta,
          })),
        };
      }

      if (event.type === 'agent_activity') {
        return { ...state, activity: event.phase };
      }

      if (event.type === 'message_completed') {
        return {
          ...state,
          messages: updatePendingAssistant(state, (message) => ({
            ...message,
            status: 'completed',
            completedAt: new Date().toISOString(),
          })),
          sending: false,
          activity: null,
          pendingClientMessageId: null,
        };
      }

      return {
        ...state,
        messages: updatePendingAssistant(state, (message) => ({
          ...message,
          status: 'failed',
          completedAt: new Date().toISOString(),
        })),
        sending: false,
        activity: null,
        pendingClientMessageId: null,
        error: `AI 回复失败：${event.errorType}`,
      };
    }

    case 'send-rejected':
      return {
        ...state,
        messages: state.messages.filter(
          (message) =>
            message.id !== `local-user-${action.clientMessageId}` &&
            message.id !== `local-assistant-${action.clientMessageId}`,
        ),
        sending: false,
        activity: null,
        pendingClientMessageId: null,
        error: action.message,
      };

    case 'transport-failed':
      return {
        ...state,
        messages: updatePendingAssistant(state, (message) => ({
          ...message,
          status: 'failed',
          completedAt: new Date().toISOString(),
        })),
        sending: false,
        activity: null,
        pendingClientMessageId: null,
        error: action.message,
      };

    case 'send-cancelled':
      return {
        ...state,
        messages: updatePendingAssistant(state, (message) => ({
          ...message,
          status: 'cancelled',
          completedAt: new Date().toISOString(),
        })),
        sending: false,
        activity: null,
        pendingClientMessageId: null,
        error: '本次回复已取消。',
      };

    case 'clear-error':
      return { ...state, error: null };
  }
}
