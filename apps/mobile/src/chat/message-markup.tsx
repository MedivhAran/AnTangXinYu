import type { ReactNode } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import type { ChatSource } from '../api/types';

export type MessageTone = 'assistant' | 'user';

type Props = {
  content: string;
  onOpenSource: (source: ChatSource) => void;
  sourcesById: ReadonlyMap<string, ChatSource>;
  tone: MessageTone;
};

type TextBlock =
  | { kind: 'paragraph'; text: string }
  | { kind: 'heading'; level: 1 | 2 | 3; text: string }
  | {
      kind: 'list';
      ordered: boolean;
      items: { marker: string; text: string }[];
    };

const headingPattern = /^(#{1,6})[ \t]+(.+?)\s*$/;
const unorderedItemPattern = /^[ \t]{0,3}[-+*][ \t]+(.+?)\s*$/;
const orderedItemPattern = /^[ \t]{0,3}(\d{1,3})[.)、][ \t]+(.+?)\s*$/;
const boldPattern = /(\*\*[^*\n]+?\*\*|__[^_\n]+?__)/g;
const citationPattern = /(\[S[1-9]\d*\])/g;
const exactCitationPattern = /^\[(S[1-9]\d*)\]$/;

function parseBlocks(content: string): TextBlock[] {
  const blocks: TextBlock[] = [];
  let paragraphLines: string[] = [];
  let activeList: Extract<TextBlock, { kind: 'list' }> | null = null;

  function flushParagraph() {
    if (paragraphLines.length === 0) return;
    blocks.push({ kind: 'paragraph', text: paragraphLines.join('\n') });
    paragraphLines = [];
  }

  function flushList() {
    if (activeList === null) return;
    blocks.push(activeList);
    activeList = null;
  }

  for (const rawLine of content.replace(/\r\n?/g, '\n').split('\n')) {
    const line = rawLine.trimEnd();
    if (line.trim().length === 0) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = headingPattern.exec(line);
    if (heading !== null) {
      flushParagraph();
      flushList();
      const markerLength = heading[1].length;
      const level = Math.min(markerLength, 3) as 1 | 2 | 3;
      const text = heading[2].replace(/[ \t]+#+[ \t]*$/, '');
      blocks.push({ kind: 'heading', level, text });
      continue;
    }

    const unorderedItem = unorderedItemPattern.exec(line);
    const orderedItem = orderedItemPattern.exec(line);
    if (unorderedItem !== null || orderedItem !== null) {
      flushParagraph();
      const ordered = orderedItem !== null;
      if (activeList !== null && activeList.ordered !== ordered) flushList();
      activeList ??= { kind: 'list', ordered, items: [] };
      activeList.items.push({
        marker: ordered ? `${orderedItem![1]}.` : '•',
        text: ordered ? orderedItem![2] : unorderedItem![1],
      });
      continue;
    }

    flushList();
    paragraphLines.push(line.trimStart());
  }

  flushParagraph();
  flushList();
  return blocks;
}

function renderCitations(
  text: string,
  sourcesById: Props['sourcesById'],
  onOpenSource: Props['onOpenSource'],
  tone: MessageTone,
  keyPrefix: string,
): ReactNode[] {
  return text.split(citationPattern).map((part, index) => {
    const sourceId = exactCitationPattern.exec(part)?.[1];
    const source = sourceId === undefined ? undefined : sourcesById.get(sourceId);
    if (source === undefined) return part;

    return (
      <Text
        accessibilityLabel={`打开来源 ${source.sourceId}`}
        accessibilityRole="link"
        key={`${keyPrefix}-citation-${index}`}
        onPress={() => onOpenSource(source)}
        style={[styles.citation, tone === 'user' && styles.userCitation]}
      >
        {part}
      </Text>
    );
  });
}

function renderInline(
  text: string,
  sourcesById: Props['sourcesById'],
  onOpenSource: Props['onOpenSource'],
  tone: MessageTone,
  keyPrefix: string,
): ReactNode[] {
  const nodes: ReactNode[] = [];

  text.split(boldPattern).forEach((part, index) => {
    const key = `${keyPrefix}-inline-${index}`;
    const isBold =
      (part.startsWith('**') && part.endsWith('**')) ||
      (part.startsWith('__') && part.endsWith('__'));
    if (isBold) {
      nodes.push(
        <Text key={key} style={styles.bold}>
          {renderCitations(
            part.slice(2, -2),
            sourcesById,
            onOpenSource,
            tone,
            key,
          )}
        </Text>,
      );
      return;
    }

    nodes.push(...renderCitations(part, sourcesById, onOpenSource, tone, key));
  });

  return nodes;
}

/** A deliberately small renderer for the stable formatting emitted in chat replies. */
export function MessageMarkup({ content, onOpenSource, sourcesById, tone }: Props) {
  const toneStyle = tone === 'user' ? styles.userText : styles.assistantText;
  const secondaryToneStyle =
    tone === 'user' ? styles.userSecondaryText : styles.assistantSecondaryText;

  return (
    <View style={styles.blocks}>
      {parseBlocks(content).map((block, blockIndex) => {
        const key = `block-${blockIndex}`;
        if (block.kind === 'heading') {
          return (
            <Text
              accessibilityRole="header"
              key={key}
              selectable
              style={[
                styles.heading,
                block.level === 1
                  ? styles.headingOne
                  : block.level === 2
                    ? styles.headingTwo
                    : styles.headingThree,
                toneStyle,
              ]}
            >
              {renderInline(block.text, sourcesById, onOpenSource, tone, key)}
            </Text>
          );
        }

        if (block.kind === 'list') {
          return (
            <View key={key} style={styles.list}>
              {block.items.map((item, itemIndex) => (
                <View key={`${key}-item-${itemIndex}`} style={styles.listItem}>
                  <Text
                    selectable
                    style={[styles.listMarker, secondaryToneStyle]}
                  >
                    {item.marker}
                  </Text>
                  <Text selectable style={[styles.bodyText, styles.listText, toneStyle]}>
                    {renderInline(
                      item.text,
                      sourcesById,
                      onOpenSource,
                      tone,
                      `${key}-item-${itemIndex}`,
                    )}
                  </Text>
                </View>
              ))}
            </View>
          );
        }

        return (
          <Text key={key} selectable style={[styles.bodyText, toneStyle]}>
            {renderInline(block.text, sourcesById, onOpenSource, tone, key)}
          </Text>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  blocks: { gap: 9 },
  bodyText: {
    fontSize: 16,
    lineHeight: 24,
    letterSpacing: 0.08,
  },
  assistantText: { color: '#1B2821' },
  userText: { color: '#F4FBF7' },
  assistantSecondaryText: { color: '#718079' },
  userSecondaryText: { color: '#CAE7D9' },
  heading: { fontWeight: '700', letterSpacing: -0.18 },
  headingOne: { fontSize: 20, lineHeight: 27 },
  headingTwo: { fontSize: 18, lineHeight: 25 },
  headingThree: { fontSize: 16, lineHeight: 23 },
  bold: { fontWeight: '700' },
  citation: {
    color: '#157D5B',
    fontWeight: '700',
    textDecorationLine: 'underline',
  },
  userCitation: { color: '#FFFFFF' },
  list: { gap: 5 },
  listItem: { flexDirection: 'row', alignItems: 'flex-start', gap: 7 },
  listMarker: {
    width: 22,
    paddingTop: 1,
    fontSize: 14,
    lineHeight: 23,
    textAlign: 'right',
    fontVariant: ['tabular-nums'],
  },
  listText: { flex: 1 },
});
