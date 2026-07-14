import { act, create } from 'react-test-renderer';

import { ActivityStatus } from './ActivityStatus';

jest.mock('react-native/Libraries/Animated/Animated', () => {
  const Animated = jest.requireActual('react-native/Libraries/Animated/Animated');
  Animated.loop = jest.fn(() => ({ start: jest.fn(), stop: jest.fn() }));
  return Animated;
});

describe('ActivityStatus', () => {
  test.each([
    ['thinking', '正在思考'],
    ['searching', '正在搜索相关资料'],
    ['reading', '正在阅读来源'],
    ['organizing', '正在整理回答'],
  ] as const)('renders the safe label for %s', async (phase, label) => {
    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(<ActivityStatus phase={phase} />);
    });

    expect(tree!.root.findByProps({ accessibilityLabel: label })).toBeTruthy();

    act(() => tree!.unmount());
  });
});
