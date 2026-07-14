import type { ChatMessage } from '../api/types';
import { chatReducer, initialChatState } from './chat-state';

const clientId = '11111111-1111-4111-8111-111111111111';
const userId = '019b1111-1111-7111-8111-111111111111';
const assistantId = '019b2222-2222-7222-8222-222222222222';
const runId = '019b3333-3333-7333-8333-333333333333';

function startedState() {
  return chatReducer(initialChatState, {
    type: 'send-started',
    clientMessageId: clientId,
    content: '我有点担心低血糖',
    now: '2026-07-13T10:00:00.000Z',
  });
}

describe('chatReducer', () => {
  test('builds an optimistic pair and appends streamed text', () => {
    let state = startedState();
    expect(state.sending).toBe(true);
    expect(state.activity).toBe('thinking');
    expect(state.messages.map((message) => message.role)).toEqual(['user', 'assistant']);

    state = chatReducer(state, {
      type: 'stream-event',
      event: {
        type: 'message_started',
        userMessageId: userId,
        assistantMessageId: assistantId,
        runId,
      },
    });
    state = chatReducer(state, {
      type: 'stream-event',
      event: { type: 'text_delta', delta: '我在这里。' },
    });
    expect(state.activity).toBeNull();
    state = chatReducer(state, {
      type: 'stream-event',
      event: { type: 'agent_activity', phase: 'organizing' },
    });
    expect(state.activity).toBe('organizing');
    state = chatReducer(state, {
      type: 'stream-event',
      event: {
        type: 'message_completed',
        inputTokens: 20,
        outputTokens: 5,
        sources: [
          {
            sourceId: 'S1',
            title: '低血糖资料',
            url: 'https://example.com/hypoglycemia',
          },
        ],
      },
    });

    expect(state.messages[0].id).toBe(userId);
    expect(state.messages[1]).toMatchObject({
      id: assistantId,
      content: '我在这里。',
      status: 'completed',
      sources: [
        {
          sourceId: 'S1',
          title: '低血糖资料',
          url: 'https://example.com/hypoglycemia',
        },
      ],
    });
    expect(state.sending).toBe(false);
    expect(state.pendingClientMessageId).toBeNull();
    expect(state.activity).toBeNull();
  });

  test('keeps partial text when the agent reports failure', () => {
    let state = startedState();
    state = chatReducer(state, {
      type: 'stream-event',
      event: { type: 'text_delta', delta: '已经生成的部分' },
    });
    state = chatReducer(state, {
      type: 'stream-event',
      event: {
        type: 'message_failed',
        code: 'agent_run_failed',
        errorType: 'ModelError',
      },
    });

    expect(state.messages[1]).toMatchObject({
      content: '已经生成的部分',
      status: 'failed',
    });
    expect(state.error).toBe('AI 回复失败：ModelError');
    expect(state.activity).toBeNull();
  });

  test('new optimistic messages start without sources', () => {
    const state = startedState();

    expect(state.messages.map((message) => message.sources)).toEqual([[], []]);
  });

  test('history with a generating assistant disables sending', () => {
    const generating: ChatMessage = {
      id: assistantId,
      clientMessageId: null,
      role: 'assistant',
      status: 'generating',
      content: '部分内容',
      sources: [],
      createdAt: '2026-07-13T10:00:00.000Z',
      completedAt: null,
    };

    const state = chatReducer(initialChatState, {
      type: 'history-replaced',
      messages: [generating],
      nextBefore: null,
    });

    expect(state.sending).toBe(true);
  });

  test('a rejected request removes only its optimistic messages', () => {
    const state = chatReducer(startedState(), {
      type: 'send-rejected',
      clientMessageId: clientId,
      message: '请求冲突',
    });

    expect(state.messages).toEqual([]);
    expect(state.error).toBe('请求冲突');
    expect(state.sending).toBe(false);
    expect(state.activity).toBeNull();
  });

  test.each([
    { action: { type: 'send-cancelled' as const }, error: '本次回复已取消。' },
    {
      action: { type: 'transport-failed' as const, message: '网络中断' },
      error: '网络中断',
    },
  ])('clears activity after $action.type', ({ action, error }) => {
    let state = startedState();
    state = chatReducer(state, {
      type: 'stream-event',
      event: { type: 'agent_activity', phase: 'reading' },
    });

    state = chatReducer(state, action);

    expect(state.activity).toBeNull();
    expect(state.sending).toBe(false);
    expect(state.error).toBe(error);
  });
});
