import { parseMessageHistory } from './types';

const assistantId = '019b2222-2222-7222-8222-222222222222';

function historyWith(sources: unknown) {
  return {
    messages: [
      {
        id: assistantId,
        client_message_id: null,
        role: 'assistant',
        status: 'completed',
        content: '请看这个来源 [S1]。',
        sources,
        created_at: '2026-07-13T10:00:00.000Z',
        completed_at: '2026-07-13T10:00:01.000Z',
      },
    ],
    next_before: null,
  };
}

describe('message history sources', () => {
  test('parses validated source metadata', () => {
    const history = parseMessageHistory(
      historyWith([
        {
          source_id: 'S1',
          title: '低血糖资料',
          url: 'https://example.com/hypoglycemia',
        },
      ]),
    );

    expect(history.messages[0].sources).toEqual([
      {
        sourceId: 'S1',
        title: '低血糖资料',
        url: 'https://example.com/hypoglycemia',
      },
    ]);
  });

  test('requires sources instead of silently guessing old history', () => {
    expect(() => parseMessageHistory(historyWith(undefined))).toThrow(
      'message.sources 必须是数组',
    );
  });

  test('rejects duplicate source identifiers', () => {
    expect(() =>
      parseMessageHistory(
        historyWith([
          { source_id: 'S1', title: '一', url: 'https://one.example' },
          { source_id: 'S1', title: '二', url: 'https://two.example' },
        ]),
      ),
    ).toThrow('message.sources 包含重复的 S1');
  });
});
