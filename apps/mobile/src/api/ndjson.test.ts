import { consumeChatStream, parseNdjsonStream } from './ndjson';
import type { ChatStreamEvent } from './types';

const userMessageId = '019b1111-1111-7111-8111-111111111111';
const assistantMessageId = '019b2222-2222-7222-8222-222222222222';
const runId = '019b3333-3333-7333-8333-333333333333';

function byteStream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    pull(controller) {
      const chunk = chunks.shift();
      if (chunk === undefined) {
        controller.close();
      } else {
        controller.enqueue(chunk);
      }
    },
  });
}

function encode(value: string): Uint8Array {
  return new TextEncoder().encode(value);
}

describe('NDJSON chat stream', () => {
  test('decodes UTF-8 text split across arbitrary byte chunks', async () => {
    const body =
      JSON.stringify({
        type: 'message_started',
        user_message_id: userMessageId,
        assistant_message_id: assistantMessageId,
        run_id: runId,
      }) +
      '\n' +
      JSON.stringify({ type: 'text_delta', delta: '低血糖' }) +
      '\n' +
      JSON.stringify({ type: 'agent_activity', phase: 'searching' }) +
      '\n' +
      JSON.stringify({
        type: 'message_completed',
        input_tokens: 10,
        output_tokens: 4,
        sources: [
          {
            source_id: 'S1',
            title: '低血糖资料',
            url: 'https://example.com/hypoglycemia',
          },
        ],
      }) +
      '\n';
    const bytes = encode(body);
    const events: ChatStreamEvent[] = [];

    await consumeChatStream(
      byteStream([
        bytes.slice(0, 7),
        bytes.slice(7, 131),
        bytes.slice(131, 134),
        bytes.slice(134),
      ]),
      (event) => events.push(event),
    );

    expect(events).toEqual([
      {
        type: 'message_started',
        userMessageId,
        assistantMessageId,
        runId,
      },
      { type: 'text_delta', delta: '低血糖' },
      { type: 'agent_activity', phase: 'searching' },
      {
        type: 'message_completed',
        inputTokens: 10,
        outputTokens: 4,
        sources: [
          {
            sourceId: 'S1',
            title: '低血糖资料',
            url: 'https://example.com/hypoglycemia',
          },
        ],
      },
    ]);
  });

  test('parses a final JSON line without a trailing newline', async () => {
    const stream = byteStream([
      encode(JSON.stringify({ type: 'text_delta', delta: '残留半行' })),
    ]);
    const events: ChatStreamEvent[] = [];

    for await (const event of parseNdjsonStream(stream)) events.push(event);

    expect(events).toEqual([{ type: 'text_delta', delta: '残留半行' }]);
  });

  test('rejects an unknown event type', async () => {
    await expect(
      consumeChatStream(byteStream([encode('{"type":"mystery"}\n')]), () => {}),
    ).rejects.toThrow('未知流事件：mystery');
  });

  test('rejects an unknown agent activity phase', async () => {
    const body =
      JSON.stringify({
        type: 'message_started',
        user_message_id: userMessageId,
        assistant_message_id: assistantMessageId,
        run_id: runId,
      }) +
      '\n' +
      JSON.stringify({ type: 'agent_activity', phase: 'guessing' }) +
      '\n';

    await expect(
      consumeChatStream(byteStream([encode(body)]), () => {}),
    ).rejects.toThrow('未知活动阶段：guessing');
  });

  test('rejects an event with missing required fields', async () => {
    await expect(
      consumeChatStream(
        byteStream([encode('{"type":"message_started"}\n')]),
        () => {},
      ),
    ).rejects.toThrow('user_message_id 必须是非空字符串');
  });

  test.each([
    [
      '非 HTTP 来源',
      { source_id: 'S1', title: '本地文件', url: 'file:///etc/passwd' },
      'sources[0].url 只允许 HTTP 或 HTTPS',
    ],
    [
      '错误的来源编号',
      { source_id: 'source-1', title: '网页', url: 'https://example.com' },
      'sources[0].source_id 必须是 S 加正整数',
    ],
  ])('rejects %s in a completed event', async (_, source, message) => {
    const body =
      JSON.stringify({
        type: 'message_started',
        user_message_id: userMessageId,
        assistant_message_id: assistantMessageId,
        run_id: runId,
      }) +
      '\n' +
      JSON.stringify({
        type: 'message_completed',
        input_tokens: 10,
        output_tokens: 4,
        sources: [source],
      }) +
      '\n';

    await expect(
      consumeChatStream(byteStream([encode(body)]), () => {}),
    ).rejects.toThrow(message);
  });

  test('rejects a stream that closes before a terminal event', async () => {
    const body =
      JSON.stringify({
        type: 'message_started',
        user_message_id: userMessageId,
        assistant_message_id: assistantMessageId,
        run_id: runId,
      }) + '\n';

    await expect(
      consumeChatStream(byteStream([encode(body)]), () => {}),
    ).rejects.toThrow('聊天流在结束事件前断开');
  });
});
