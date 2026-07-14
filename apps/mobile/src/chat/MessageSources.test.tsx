import { Linking } from 'react-native';
import { act, create } from 'react-test-renderer';

import { MessageSources } from './MessageSources';

const source = {
  sourceId: 'S1',
  title: '低血糖资料',
  url: 'https://example.com/hypoglycemia',
};

describe('MessageSources', () => {
  afterEach(() => jest.restoreAllMocks());

  test('links only strict citations backed by known sources', async () => {
    const openUrl = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <MessageSources
          content="有效 [S1]，未知 [S2]，非严格 [S01]。"
          onOpenError={jest.fn()}
          sources={[source]}
        />,
      );
    });

    const inlineCitation = tree!.root.findByProps({
      accessibilityLabel: '打开来源 S1',
    });
    expect(tree!.root.findAllByProps({ accessibilityLabel: '打开来源 S2' })).toHaveLength(
      0,
    );

    await act(async () => inlineCitation.props.onPress());
    expect(openUrl).toHaveBeenCalledWith(source.url);
    act(() => tree!.unmount());
  });

  test('shows the title and full URL in a clickable source card', async () => {
    const openUrl = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <MessageSources content="回答 [S1]" onOpenError={jest.fn()} sources={[source]} />,
      );
    });

    expect(tree!.root.findByProps({ children: source.url })).toBeTruthy();
    const card = tree!.root.findByProps({
      accessibilityLabel: `打开来源 S1：${source.title}`,
    });
    await act(async () => card.props.onPress());
    expect(openUrl).toHaveBeenCalledWith(source.url);
    act(() => tree!.unmount());
  });

  test('reports an open failure through the caller', async () => {
    jest.spyOn(Linking, 'openURL').mockRejectedValue(new Error('没有浏览器'));
    const onOpenError = jest.fn();
    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <MessageSources content="回答 [S1]" onOpenError={onOpenError} sources={[source]} />,
      );
    });

    const card = tree!.root.findByProps({
      accessibilityLabel: `打开来源 S1：${source.title}`,
    });
    await act(async () => {
      card.props.onPress();
      await Promise.resolve();
    });

    expect(onOpenError).toHaveBeenCalledWith('无法打开来源 S1：没有浏览器');
    act(() => tree!.unmount());
  });
});
