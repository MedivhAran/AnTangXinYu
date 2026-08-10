import { Linking, StyleSheet, Text } from 'react-native';
import {
  act,
  create,
  type ReactTestInstance,
  type ReactTestRenderer,
} from 'react-test-renderer';

import { MessageSources } from './MessageSources';

const source = {
  sourceId: 'S1',
  title: '低血糖资料',
  url: 'https://www.example.com/hypoglycemia?from=a-very-long-address',
};

function textContent(node: ReactTestInstance): string {
  return node.children
    .map((child) => (typeof child === 'string' ? child : textContent(child)))
    .join('');
}

async function renderMessage(
  props: Partial<React.ComponentProps<typeof MessageSources>> = {},
) {
  let tree: ReactTestRenderer;
  await act(async () => {
    tree = create(
      <MessageSources
        content="回答 [S1]"
        onOpenError={jest.fn()}
        sources={[source]}
        {...props}
      />,
    );
  });
  return tree!;
}

describe('MessageSources', () => {
  afterEach(() => jest.restoreAllMocks());

  test('links only strict citations backed by known sources', async () => {
    const openUrl = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    const tree = await renderMessage({
      content:
        '有效 [S1]，未知 [S2]，非严格 [S01]，普通 https://attacker.example。',
    });

    const inlineCitation = tree.root.findByProps({
      accessibilityLabel: '打开来源 S1',
    });
    expect(tree.root.findAllByProps({ accessibilityLabel: '打开来源 S2' })).toHaveLength(
      0,
    );

    await act(async () => inlineCitation.props.onPress());
    expect(openUrl).toHaveBeenCalledWith(source.url);
    act(() => tree.unmount());
  });

  test('renders headings, lists, and bold text without their raw markers', async () => {
    const tree = await renderMessage({
      content: [
        '# 先看重点',
        '',
        '这是**需要留意**的部分。',
        '- 第一项',
        '* 第二项 [S1]',
        '1. 有序一',
        '2) 有序二',
      ].join('\n'),
    });

    const heading = tree.root.findByProps({ accessibilityRole: 'header' });
    expect(textContent(heading)).toBe('先看重点');

    const bold = tree.root
      .findAllByType(Text)
      .find((node) => textContent(node) === '需要留意');
    expect(bold).toBeDefined();
    expect(StyleSheet.flatten(bold!.props.style).fontWeight).toBe('700');

    const rendered = textContent(tree.root);
    expect(rendered).toContain('•第一项');
    expect(rendered).toContain('1.有序一');
    expect(rendered).not.toContain('# 先看重点');
    expect(rendered).not.toContain('**');
    expect(rendered).not.toContain('- 第一项');
    expect(rendered).not.toContain('* 第二项');
    act(() => tree.unmount());
  });

  test('does not turn ordinary or markdown-style HTTP URLs into links', async () => {
    const content =
      '普通链接 https://attacker.example 和 [点这里](https://attacker.example)';
    const tree = await renderMessage({
      content,
      sources: [],
    });

    expect(tree.root.findAllByProps({ accessibilityRole: 'link' })).toHaveLength(0);
    const body = tree.root
      .findAllByType(Text)
      .find((node) => textContent(node) === content);
    expect(body?.props.onPress).toBeUndefined();
    act(() => tree.unmount());
  });

  test('keeps sources collapsed, then shows title and domain instead of the URL', async () => {
    const openUrl = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    const tree = await renderMessage();

    const expand = tree.root.findByProps({
      accessibilityLabel: '展开参考来源，共 1 条',
    });
    expect(expand.props.accessibilityState).toEqual({ expanded: false });
    expect(
      tree.root.findAllByProps({
        accessibilityLabel: `打开来源 S1：${source.title}`,
      }),
    ).toHaveLength(0);

    await act(async () => expand.props.onPress());
    expect(tree.root.findByProps({ children: source.title })).toBeTruthy();
    expect(tree.root.findByProps({ children: 'example.com' })).toBeTruthy();
    expect(tree.root.findAllByProps({ children: source.url })).toHaveLength(0);

    const card = tree.root.findByProps({
      accessibilityLabel: `打开来源 S1：${source.title}`,
    });
    await act(async () => card.props.onPress());
    expect(openUrl).toHaveBeenCalledWith(source.url);

    const collapse = tree.root.findByProps({
      accessibilityLabel: '收起参考来源，共 1 条',
    });
    expect(collapse.props.accessibilityState).toEqual({ expanded: true });
    await act(async () => collapse.props.onPress());
    expect(
      tree.root.findAllByProps({
        accessibilityLabel: `打开来源 S1：${source.title}`,
      }),
    ).toHaveLength(0);
    act(() => tree.unmount());
  });

  test('reports an open failure through the caller', async () => {
    jest.spyOn(Linking, 'openURL').mockRejectedValue(new Error('没有浏览器'));
    const onOpenError = jest.fn();
    const tree = await renderMessage({ onOpenError });

    const citation = tree.root.findByProps({
      accessibilityLabel: '打开来源 S1',
    });
    await act(async () => {
      citation.props.onPress();
      await Promise.resolve();
    });

    expect(onOpenError).toHaveBeenCalledWith('无法打开来源 S1：没有浏览器');
    act(() => tree.unmount());
  });

  test('uses assistant tone by default and a light foreground for user messages', async () => {
    const assistant = await renderMessage({ content: '你好', sources: [] });
    const user = await renderMessage({ content: '你好', sources: [], tone: 'user' });

    const assistantText = assistant.root
      .findAllByType(Text)
      .find((node) => textContent(node) === '你好');
    const userText = user.root
      .findAllByType(Text)
      .find((node) => textContent(node) === '你好');

    expect(StyleSheet.flatten(assistantText!.props.style).color).toBe('#1B2821');
    expect(StyleSheet.flatten(userText!.props.style).color).toBe('#F4FBF7');
    act(() => {
      assistant.unmount();
      user.unmount();
    });
  });
});
