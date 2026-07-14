import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';

import type { ChatSource } from '../api/types';

type Props = {
  content: string;
  sources: ChatSource[];
  onOpenError: (message: string) => void;
};

const citationPattern = /(\[S[1-9]\d*\])/g;
const exactCitationPattern = /^\[(S[1-9]\d*)\]$/;

function openSource(source: ChatSource, onOpenError: Props['onOpenError']) {
  void Linking.openURL(source.url).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '未知错误';
    onOpenError(`无法打开来源 ${source.sourceId}：${detail}`);
  });
}

/** 只把后端确认过、并且出现在 sources 中的严格 [S数字] 变成链接。 */
export function MessageSources({ content, sources, onOpenError }: Props) {
  const sourcesById = new Map(sources.map((source) => [source.sourceId, source]));

  return (
    <>
      <Text style={styles.messageText}>
        {content.split(citationPattern).map((part, index) => {
          const sourceId = exactCitationPattern.exec(part)?.[1];
          const source = sourceId === undefined ? undefined : sourcesById.get(sourceId);
          if (source === undefined) return part;

          return (
            <Text
              accessibilityLabel={`打开来源 ${source.sourceId}`}
              accessibilityRole="link"
              key={`${source.sourceId}-${index}`}
              onPress={() => openSource(source, onOpenError)}
              style={styles.citation}
            >
              {part}
            </Text>
          );
        })}
      </Text>

      {sources.length === 0 ? null : (
        <View style={styles.sources}>
          <Text style={styles.sourcesTitle}>来源</Text>
          {sources.map((source) => (
            <Pressable
              accessibilityLabel={`打开来源 ${source.sourceId}：${source.title}`}
              accessibilityRole="link"
              key={source.sourceId}
              onPress={() => openSource(source, onOpenError)}
              style={({ pressed }) => [styles.source, pressed && styles.sourcePressed]}
            >
              <Text style={styles.sourceTitle}>
                {source.sourceId} · {source.title}
              </Text>
              <Text style={styles.sourceUrl}>
                {source.url}
              </Text>
            </Pressable>
          ))}
        </View>
      )}
    </>
  );
}

const styles = StyleSheet.create({
  messageText: { color: '#17211C', fontSize: 16, lineHeight: 23 },
  citation: { color: '#157D5B', fontWeight: '700', textDecorationLine: 'underline' },
  sources: {
    marginTop: 10,
    paddingTop: 9,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#E1E7E4',
    gap: 7,
  },
  sourcesTitle: { color: '#65716B', fontSize: 12, fontWeight: '600' },
  source: { paddingVertical: 2 },
  sourcePressed: { opacity: 0.6 },
  sourceTitle: { color: '#155F49', fontSize: 13, fontWeight: '600', lineHeight: 18 },
  sourceUrl: { marginTop: 1, color: '#6D7A74', fontSize: 11, lineHeight: 15 },
});
