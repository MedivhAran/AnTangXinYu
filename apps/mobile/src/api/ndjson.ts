import { parseChatStreamEvent, type ChatStreamEvent } from './types';

function parseLine(line: string, lineNumber: number): ChatStreamEvent {
  if (line.length === 0) throw new Error(`NDJSON 第 ${lineNumber} 行为空`);

  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    throw new Error(`NDJSON 第 ${lineNumber} 行不是有效 JSON`);
  }
  return parseChatStreamEvent(value);
}

/** 按 UTF-8 解码字节流，并保留跨网络分块的半行内容。 */
export async function* parseNdjsonStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<ChatStreamEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let pending = '';
  let lineNumber = 0;
  let completed = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      pending += decoder.decode(value, { stream: true });
      const lines = pending.split('\n');
      pending = lines.pop() ?? '';

      for (const rawLine of lines) {
        lineNumber += 1;
        const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
        yield parseLine(line, lineNumber);
      }
    }

    pending += decoder.decode();
    if (pending.length > 0) {
      lineNumber += 1;
      const line = pending.endsWith('\r') ? pending.slice(0, -1) : pending;
      yield parseLine(line, lineNumber);
    }
    completed = true;
  } finally {
    if (!completed) await reader.cancel();
    reader.releaseLock();
  }
}

/** 校验聊天流的开始与结束顺序，流提前关闭会明确失败。 */
export async function consumeChatStream(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  let phase: 'waiting' | 'streaming' | 'ended' = 'waiting';

  for await (const event of parseNdjsonStream(stream)) {
    if (phase === 'ended') throw new Error('聊天流在结束事件后仍有数据');

    if (phase === 'waiting') {
      if (event.type !== 'message_started') {
        throw new Error('聊天流缺少 message_started 事件');
      }
      phase = 'streaming';
      onEvent(event);
      continue;
    }

    if (event.type === 'message_started') {
      throw new Error('聊天流重复发送 message_started 事件');
    }

    onEvent(event);
    if (event.type === 'message_completed' || event.type === 'message_failed') {
      phase = 'ended';
    }
  }

  if (phase !== 'ended') throw new Error('聊天流在结束事件前断开');
}
