export type User = {
  id: string;
  username: string;
};

export type TokenPair = {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
};

export type AuthResult = {
  user: User;
  tokens: TokenPair;
};

export type MessageRole = 'user' | 'assistant';
export type MessageStatus = 'generating' | 'completed' | 'failed' | 'cancelled';

export type ChatSource = {
  sourceId: string;
  title: string;
  url: string;
};

export type ChatMessage = {
  id: string;
  clientMessageId: string | null;
  role: MessageRole;
  status: MessageStatus;
  content: string;
  sources: ChatSource[];
  createdAt: string;
  completedAt: string | null;
};

export type MessageHistory = {
  messages: ChatMessage[];
  nextBefore: string | null;
};

export type MessageStartedEvent = {
  type: 'message_started';
  userMessageId: string;
  assistantMessageId: string;
  runId: string;
};

export type TextDeltaEvent = {
  type: 'text_delta';
  delta: string;
};

export type AgentActivityPhase =
  | 'thinking'
  | 'searching'
  | 'reading'
  | 'organizing';

export type AgentActivityEvent = {
  type: 'agent_activity';
  phase: AgentActivityPhase;
};

export type MessageCompletedEvent = {
  type: 'message_completed';
  inputTokens: number;
  outputTokens: number;
  sources: ChatSource[];
};

export type MessageFailedEvent = {
  type: 'message_failed';
  code: 'agent_run_failed';
  errorType: string;
};

export type ChatStreamEvent =
  | MessageStartedEvent
  | TextDeltaEvent
  | AgentActivityEvent
  | MessageCompletedEvent
  | MessageFailedEvent;

type JsonObject = Record<string, unknown>;

const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function objectValue(value: unknown, name: string): JsonObject {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`${name} 必须是 JSON 对象`);
  }
  return value as JsonObject;
}

function stringValue(value: unknown, name: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${name} 必须是非空字符串`);
  }
  return value;
}

function uuidValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  if (!uuidPattern.test(result)) throw new Error(`${name} 必须是 UUID`);
  return result;
}

function integerValue(value: unknown, name: string): number {
  if (!Number.isInteger(value) || (value as number) < 0) {
    throw new Error(`${name} 必须是非负整数`);
  }
  return value as number;
}

function dateValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  if (Number.isNaN(Date.parse(result))) throw new Error(`${name} 必须是日期时间`);
  return result;
}

function httpUrlValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  let parsed: URL;
  try {
    parsed = new URL(result);
  } catch {
    throw new Error(`${name} 必须是有效 URL`);
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error(`${name} 只允许 HTTP 或 HTTPS`);
  }
  return result;
}

function parseSources(value: unknown, name: string): ChatSource[] {
  if (!Array.isArray(value)) throw new Error(`${name} 必须是数组`);

  const knownIds = new Set<string>();
  return value.map((source, index) => {
    const data = objectValue(source, `${name}[${index}]`);
    const sourceId = stringValue(data.source_id, `${name}[${index}].source_id`);
    if (!/^S[1-9]\d*$/.test(sourceId)) {
      throw new Error(`${name}[${index}].source_id 必须是 S 加正整数`);
    }
    if (knownIds.has(sourceId)) throw new Error(`${name} 包含重复的 ${sourceId}`);
    knownIds.add(sourceId);

    return {
      sourceId,
      title: stringValue(data.title, `${name}[${index}].title`),
      url: httpUrlValue(data.url, `${name}[${index}].url`),
    };
  });
}

export function parseUser(value: unknown): User {
  const data = objectValue(value, 'user');
  return {
    id: uuidValue(data.id, 'user.id'),
    username: stringValue(data.username, 'user.username'),
  };
}

export function parseTokenPair(value: unknown): TokenPair {
  const data = objectValue(value, 'token pair');
  return {
    accessToken: stringValue(data.access_token, 'access_token'),
    refreshToken: stringValue(data.refresh_token, 'refresh_token'),
    expiresIn: integerValue(data.expires_in, 'expires_in'),
  };
}

export function parseAuthResult(value: unknown): AuthResult {
  const data = objectValue(value, 'auth response');
  return {
    user: parseUser(data.user),
    tokens: parseTokenPair(data),
  };
}

function parseMessage(value: unknown): ChatMessage {
  const data = objectValue(value, 'message');
  const role = stringValue(data.role, 'message.role');
  const status = stringValue(data.status, 'message.status');

  if (role !== 'user' && role !== 'assistant') {
    throw new Error(`未知消息角色：${role}`);
  }
  if (!['generating', 'completed', 'failed', 'cancelled'].includes(status)) {
    throw new Error(`未知消息状态：${status}`);
  }
  if (typeof data.content !== 'string') {
    throw new Error('message.content 必须是字符串');
  }

  return {
    id: uuidValue(data.id, 'message.id'),
    clientMessageId:
      data.client_message_id === null
        ? null
        : uuidValue(data.client_message_id, 'message.client_message_id'),
    role,
    status: status as MessageStatus,
    content: data.content,
    sources: parseSources(data.sources, 'message.sources'),
    createdAt: dateValue(data.created_at, 'message.created_at'),
    completedAt:
      data.completed_at === null
        ? null
        : dateValue(data.completed_at, 'message.completed_at'),
  };
}

export function parseMessageHistory(value: unknown): MessageHistory {
  const data = objectValue(value, 'message history');
  if (!Array.isArray(data.messages)) {
    throw new Error('message history.messages 必须是数组');
  }
  return {
    messages: data.messages.map(parseMessage),
    nextBefore:
      data.next_before === null
        ? null
        : uuidValue(data.next_before, 'message history.next_before'),
  };
}

export function parseChatStreamEvent(value: unknown): ChatStreamEvent {
  const data = objectValue(value, 'stream event');
  const type = stringValue(data.type, 'stream event.type');

  switch (type) {
    case 'message_started':
      return {
        type,
        userMessageId: uuidValue(data.user_message_id, 'user_message_id'),
        assistantMessageId: uuidValue(
          data.assistant_message_id,
          'assistant_message_id',
        ),
        runId: uuidValue(data.run_id, 'run_id'),
      };
    case 'text_delta':
      return {
        type,
        delta: stringValue(data.delta, 'delta'),
      };
    case 'agent_activity': {
      const phase = stringValue(data.phase, 'phase');
      if (!['thinking', 'searching', 'reading', 'organizing'].includes(phase)) {
        throw new Error(`未知活动阶段：${phase}`);
      }
      return { type, phase: phase as AgentActivityPhase };
    }
    case 'message_completed':
      return {
        type,
        inputTokens: integerValue(data.input_tokens, 'input_tokens'),
        outputTokens: integerValue(data.output_tokens, 'output_tokens'),
        sources: parseSources(data.sources, 'sources'),
      };
    case 'message_failed': {
      const code = stringValue(data.code, 'code');
      if (code !== 'agent_run_failed') throw new Error(`未知失败代码：${code}`);
      return {
        type,
        code,
        errorType: stringValue(data.error_type, 'error_type'),
      };
    }
    default:
      throw new Error(`未知流事件：${type}`);
  }
}

export function readErrorPayload(value: unknown): {
  code?: string;
  message?: string;
} {
  const data = objectValue(value, 'error response');
  return {
    code: typeof data.code === 'string' ? data.code : undefined,
    message:
      typeof data.message === 'string'
        ? data.message
        : typeof data.detail === 'string'
          ? data.detail
          : undefined,
  };
}
