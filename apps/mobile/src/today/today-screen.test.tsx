import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  View,
} from 'react-native';
import { act, create } from 'react-test-renderer';

import type { HealthProfile } from '../api/types';
import { TodayScreen } from './today-screen';

void [ActivityIndicator, Pressable, RefreshControl, ScrollView, Text, View];

const profile: HealthProfile = {
  personalProfile: {
    sex: null,
    ageYears: null,
    ageAsOfDate: null,
    heightCm: null,
    weightKg: null,
    residentArea: null,
    scheduleType: null,
    occupation: null,
    revision: 0,
    updatedAt: '2026-08-08T09:00:00+08:00',
  },
  healthFacts: [],
  wearableLatest: [
    {
      recordType: 'heart_rate',
      observedAt: '2026-08-08T08:30:00+08:00',
      sourcePackage: 'com.huami.watch.hmwatchmanager',
      data: {
        samples: [
          { time: '2026-08-08T08:29:00+08:00', beatsPerMinute: 72 },
          { time: '2026-08-08T08:30:00+08:00', beatsPerMinute: 76 },
        ],
      },
    },
    {
      recordType: 'steps',
      observedAt: '2026-08-08T08:20:00+08:00',
      sourcePackage: 'com.huami.watch.hmwatchmanager',
      data: { count: 1286 },
    },
    {
      recordType: 'oxygen_saturation',
      observedAt: '2026-08-08T08:10:00+08:00',
      sourcePackage: 'com.huami.watch.hmwatchmanager',
      data: { percentage: 98 },
    },
    {
      recordType: 'sleep',
      observedAt: '2026-08-08T07:00:00+08:00',
      sourcePackage: 'com.huami.watch.hmwatchmanager',
      data: {
        stages: [
          {
            startTime: '2026-08-08T00:00:00+08:00',
            endTime: '2026-08-08T03:00:00+08:00',
            stage: 'deep',
          },
          {
            startTime: '2026-08-08T03:00:00+08:00',
            endTime: '2026-08-08T07:00:00+08:00',
            stage: 'light',
          },
        ],
      },
    },
  ],
};

describe('TodayScreen', () => {
  test('separates demo glucose from real wearable history', async () => {
    const api = { getHealthProfile: jest.fn().mockResolvedValue(profile) };
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <TodayScreen
          api={api}
          onClose={jest.fn()}
          onOpenCare={jest.fn()}
          onOpenHealth={jest.fn()}
          onStartConversation={jest.fn()}
          username="小安"
        />,
      );
    });

    expect(api.getHealthProfile).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(tree!.root.findByProps({ children: '演示数据' })).toBeTruthy();
    expect(
      tree!.root.findByProps({
        children: '这条曲线用于展示界面，不来自设备，也不用于医疗判断。',
      }),
    ).toBeTruthy();
    expect(tree!.root.findByProps({ children: '76' })).toBeTruthy();
    expect(tree!.root.findByProps({ children: '1,286' })).toBeTruthy();
    expect(tree!.root.findByProps({ children: '98' })).toBeTruthy();
    expect(tree!.root.findByProps({ children: '7.0' })).toBeTruthy();
    expect(
      tree!.root.findAll(
        (node) =>
          typeof node.props.children === 'string' &&
          node.props.children.includes('不是实时测量'),
      ).length,
    ).toBeGreaterThan(0);
    act(() => tree!.unmount());
  });

  test('keeps demo interactions local until the user chooses to talk', async () => {
    const onStartConversation = jest.fn();
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <TodayScreen
          api={{ getHealthProfile: jest.fn().mockResolvedValue(profile) }}
          onClose={jest.fn()}
          onOpenCare={jest.fn()}
          onOpenHealth={jest.fn()}
          onStartConversation={onStartConversation}
          username="demo@example.com"
        />,
      );
    });

    act(() => {
      tree!.root.findByProps({ accessibilityLabel: '晚饭后轻松走一会儿' }).props.onPress();
      tree!.root.findByProps({ accessibilityLabel: '有点担心' }).props.onPress();
    });
    expect(
      tree!.root.findByProps({ accessibilityLabel: '晚饭后轻松走一会儿' }).props
        .accessibilityState,
    ).toEqual({ checked: true });
    expect(onStartConversation).not.toHaveBeenCalled();

    act(() => {
      tree!.root.findByProps({ accessibilityLabel: '带着这个感受聊一聊' }).props.onPress();
    });
    expect(onStartConversation).toHaveBeenCalledWith(
      '我现在想到低血糖有点担心，陪我梳理一下好吗？',
    );
    act(() => tree!.unmount());
  });

  test('shows health loading failures and retries explicitly', async () => {
    const api = {
      getHealthProfile: jest
        .fn()
        .mockRejectedValueOnce(new Error('服务器无法连接'))
        .mockResolvedValueOnce(profile),
    };
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <TodayScreen
          api={api}
          onClose={jest.fn()}
          onOpenCare={jest.fn()}
          onOpenHealth={jest.fn()}
          onStartConversation={jest.fn()}
          username="demo"
        />,
      );
    });
    expect(tree!.root.findByProps({ children: '服务器无法连接' })).toBeTruthy();

    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '重试读取健康记录' }).props.onPress();
    });
    expect(api.getHealthProfile).toHaveBeenCalledTimes(2);
    expect(tree!.root.findByProps({ children: '76' })).toBeTruthy();
    act(() => tree!.unmount());
  });
});
