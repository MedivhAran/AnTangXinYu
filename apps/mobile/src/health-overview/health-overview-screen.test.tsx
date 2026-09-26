import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  View,
} from 'react-native';
import { act, create } from 'react-test-renderer';

import { ApiError, type ApiClient } from '../api/client';
import type { HealthProfile } from '../api/types';
import { HealthOverviewScreen, sourceLabel } from './health-overview-screen';

jest.mock('expo-crypto', () => ({
  randomUUID: jest.fn(() => '019b9999-9999-7999-8999-999999999999'),
}));

const randomUUIDMock = jest.requireMock('expo-crypto').randomUUID as jest.Mock;

// Resolve React Native's lazy exports while Jest is loading this module.
void [ActivityIndicator, Pressable, RefreshControl, ScrollView, Text, View];

const profile: HealthProfile = {
  personalProfile: {
    sex: '女',
    ageYears: 28,
    ageAsOfDate: '2026-07-01',
    heightCm: 165.2,
    weightKg: 58.3,
    residentArea: '上海',
    scheduleType: '规律作息',
    occupation: '设计师',
    revision: 3,
    updatedAt: '2026-07-26T12:00:00+08:00',
  },
  healthFacts: [
    {
      id: '019b7777-7777-7777-8777-777777777777',
      factType: 'treatment',
      statement: '目前使用胰岛素泵',
      assertion: 'present',
      temporalStatus: 'current',
      effectiveStart: '2025-09-01',
      effectiveEnd: null,
      revision: 2,
      createdAt: '2026-07-20T10:00:00+08:00',
      updatedAt: '2026-07-25T10:00:00+08:00',
    },
  ],
  heartRateTrend: [
    { observedAt: '2026-07-26T18:00:00+08:00', beatsPerMinute: 72 },
    { observedAt: '2026-07-26T20:00:00+08:00', beatsPerMinute: 81 },
    { observedAt: '2026-07-26T21:59:00+08:00', beatsPerMinute: 78 },
  ],
  wearableLatest: [
    {
      recordType: 'heart_rate',
      observedAt: '2026-07-26T22:00:00+08:00',
      sourcePackage: 'com.huami.watch.hmwatchmanager',
      data: {
        samples: [
          { time: '2026-07-26T21:58:00+08:00', beatsPerMinute: 76 },
          { time: '2026-07-26T21:59:00+08:00', beatsPerMinute: 78 },
        ],
      },
    },
    {
      recordType: 'steps',
      observedAt: '2026-07-26T21:50:00+08:00',
      sourcePackage: 'com.huami.watch.hmwatchmanager',
      data: { count: 842 },
    },
    {
      recordType: 'sleep',
      observedAt: '2026-07-26T07:00:00+08:00',
      sourcePackage: 'com.huami.watch.hmwatchmanager',
      data: {
        stages: [
          {
            startTime: '2026-07-25T23:00:00+08:00',
            endTime: '2026-07-26T02:00:00+08:00',
            stage: 'deep',
          },
          {
            startTime: '2026-07-26T03:00:00+08:00',
            endTime: '2026-07-26T07:00:00+08:00',
            stage: 'light',
          },
        ],
      },
    },
  ],
};

const emptyProfile: HealthProfile = {
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
    updatedAt: '2026-07-26T12:00:00+08:00',
  },
  healthFacts: [],
  heartRateTrend: [],
  wearableLatest: [],
};

describe('HealthOverviewScreen', () => {
  beforeEach(() => randomUUIDMock.mockClear());
  test('renders only real profile and wearable values with non-real-time context', async () => {
    const api = {
      getHealthProfile: jest.fn().mockResolvedValue(profile),
    } as unknown as ApiClient;
    const onClose = jest.fn();
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(<HealthOverviewScreen api={api} onClose={onClose} />);
    });

    expect(api.getHealthProfile).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(
      tree!.root.findByProps({ children: '历史记录 · 非实时' }),
    ).toBeTruthy();
    expect(tree!.root.findByProps({ children: '78' })).toBeTruthy();
    expect(
      tree!.root.findByProps({
        children: '单条步数记录，并非今日总量',
      }),
    ).toBeTruthy();
    expect(tree!.root.findByProps({ children: '58.3 kg' })).toBeTruthy();
    expect(
      tree!.root.findByProps({ children: '目前使用胰岛素泵' }),
    ).toBeTruthy();
    expect(
      tree!.root.findByProps({
        accessibilityLabel: '最近 6 小时心率趋势：共 3 个采样点，范围 72–81 次/分',
      }),
    ).toBeTruthy();
    expect(
      tree!.root.findByProps({
        accessibilityLabel:
          '睡眠阶段时间轴：深睡、浅睡，包含未记录间隔',
      }),
    ).toBeTruthy();
    expect(tree!.root.findByProps({ children: '未记录' })).toBeTruthy();
    expect(
      tree!.root.findAll(
        (node) =>
          typeof node.props.children === 'string' &&
          node.props.children.includes('范围 72–81 次/分'),
      ).length,
    ).toBeGreaterThan(0);

    act(() => {
      tree!.root
        .findByProps({ accessibilityLabel: '关闭我的健康' })
        .props.onPress();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    act(() => tree!.unmount());
  });

  test('gives each empty section an honest next step', async () => {
    const api = {
      getHealthProfile: jest.fn().mockResolvedValue(emptyProfile),
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <HealthOverviewScreen api={api} onClose={jest.fn()} />,
      );
    });

    expect(tree!.root.findByProps({ children: '还没有设备记录' })).toBeTruthy();
    expect(
      tree!.root.findByProps({ accessibilityLabel: '填写性别' }),
    ).toBeTruthy();
    expect(
      tree!.root.findByProps({ accessibilityLabel: '填写职业' }),
    ).toBeTruthy();
    expect(tree!.root.findByProps({ children: '这里还是空的' })).toBeTruthy();
    act(() => tree!.unmount());
  });

  test('syncs Health Connect before refreshing the displayed readings', async () => {
    let resolveSync!: () => void;
    const syncPromise = new Promise<void>((resolve) => {
      resolveSync = resolve;
    });
    const refreshedProfile: HealthProfile = {
      ...profile,
      wearableLatest: [
        {
          recordType: 'heart_rate',
          observedAt: '2026-07-26T22:05:00+08:00',
          sourcePackage: 'com.huami.watch.hmwatchmanager',
          data: {
            samples: [
              {
                time: '2026-07-26T22:04:00+08:00',
                beatsPerMinute: 82,
              },
            ],
          },
        },
      ],
    };
    const api = {
      getHealthProfile: jest
        .fn()
        .mockResolvedValueOnce(profile)
        .mockResolvedValueOnce(refreshedProfile),
    } as unknown as ApiClient;
    const onSyncWearable = jest.fn().mockReturnValue(syncPromise);
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <HealthOverviewScreen
          api={api}
          onClose={jest.fn()}
          onSyncWearable={onSyncWearable}
        />,
      );
    });
    await act(async () => {
      tree!.root
        .findByProps({ accessibilityLabel: '同步 Health Connect 数据' })
        .props.onPress();
      await Promise.resolve();
    });

    expect(onSyncWearable).toHaveBeenCalledTimes(1);
    expect(api.getHealthProfile).toHaveBeenCalledTimes(1);
    expect(
      tree!.root.findByProps({ accessibilityLabel: '同步 Health Connect 数据' }).props
        .accessibilityState,
    ).toEqual({ busy: true, disabled: true });
    expect(tree!.root.findByProps({ children: '78' })).toBeTruthy();

    await act(async () => {
      resolveSync();
      await syncPromise;
    });
    expect(api.getHealthProfile).toHaveBeenCalledTimes(2);
    expect(tree!.root.findByProps({ children: '82' })).toBeTruthy();
    act(() => tree!.unmount());
  });

  test('does not draw an old single sample as a recent trend and reloads after sync', async () => {
    const stale: HealthProfile = {
      ...profile,
      heartRateTrend: [],
      wearableLatest: [{
        recordType: 'heart_rate',
        observedAt: '2026-09-25T07:37:00Z',
        sourcePackage: 'com.huami.watch.hmwatchmanager',
        data: { samples: [{ time: '2026-09-25T07:37:00Z', beatsPerMinute: 72 }] },
      }],
    };
    const current: HealthProfile = {
      ...stale,
      heartRateTrend: [
        { observedAt: '2026-09-26T12:00:00Z', beatsPerMinute: 76 },
        { observedAt: '2026-09-26T12:05:00Z', beatsPerMinute: 82 },
      ],
      wearableLatest: [{
        recordType: 'heart_rate',
        observedAt: '2026-09-26T12:05:00Z',
        sourcePackage: 'com.huami.watch.hmwatchmanager',
        data: { samples: [{ time: '2026-09-26T12:05:00Z', beatsPerMinute: 82 }] },
      }],
    };
    const api = {
      getHealthProfile: jest.fn().mockResolvedValueOnce(stale).mockResolvedValueOnce(current),
    } as unknown as ApiClient;
    const onClose = jest.fn();
    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <HealthOverviewScreen api={api} onClose={onClose} wearableSyncCompletedAt={null} />,
      );
    });
    expect(tree!.root.findAll((node) =>
      typeof node.props.children === 'string' &&
      node.props.children.includes('近 6 小时无新采样')).length).toBeGreaterThan(0);
    expect(tree!.root.findAll((node) =>
      typeof node.props.accessibilityLabel === 'string' &&
      node.props.accessibilityLabel.startsWith('最近 6 小时心率趋势')).length).toBe(0);

    await act(async () => {
      tree!.update(
        <HealthOverviewScreen
          api={api}
          onClose={onClose}
          wearableSyncCompletedAt="2026-09-26T12:06:00Z"
        />,
      );
    });
    expect(api.getHealthProfile).toHaveBeenCalledTimes(2);
    expect(tree!.root.findByProps({ children: '82' })).toBeTruthy();
    act(() => tree!.unmount());
  });

  test('shows a Health Connect sync error instead of reloading stale readings', async () => {
    const api = { getHealthProfile: jest.fn().mockResolvedValue(profile) } as unknown as ApiClient;
    const onSyncWearable = jest.fn().mockRejectedValue(new Error('Health Connect 读取失败'));
    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <HealthOverviewScreen
          api={api}
          onClose={jest.fn()}
          onSyncWearable={onSyncWearable}
        />,
      );
    });
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '同步 Health Connect 数据' }).props.onPress();
    });
    expect(api.getHealthProfile).toHaveBeenCalledTimes(1);
    expect(tree!.root.findByProps({ children: 'Health Connect 读取失败' })).toBeTruthy();
    act(() => tree!.unmount());
  });

  test('shows a readable error and retries without needing to reopen the screen', async () => {
    const api = {
      getHealthProfile: jest
        .fn()
        .mockRejectedValueOnce(new Error('网络暂时不可用'))
        .mockResolvedValueOnce(emptyProfile),
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <HealthOverviewScreen api={api} onClose={jest.fn()} />,
      );
    });
    expect(
      tree!.root.findByProps({ children: '健康记录没有加载成功' }),
    ).toBeTruthy();
    expect(tree!.root.findByProps({ children: '网络暂时不可用' })).toBeTruthy();

    await act(async () => {
      tree!.root
        .findByProps({ accessibilityLabel: '重新加载健康记录' })
        .props.onPress();
    });

    expect(api.getHealthProfile).toHaveBeenCalledTimes(2);
    expect(tree!.root.findByProps({ children: '还没有设备记录' })).toBeTruthy();
    act(() => tree!.unmount());
  });

  test('edits and clears personal fields with the current profile revision', async () => {
    const updatedProfile: HealthProfile = {
      ...profile,
      personalProfile: {
        ...profile.personalProfile,
        heightCm: 169,
        revision: 4,
      },
    };
    const api = {
      getHealthProfile: jest
        .fn()
        .mockResolvedValueOnce(profile)
        .mockResolvedValueOnce(updatedProfile)
        .mockResolvedValueOnce({
          ...updatedProfile,
          personalProfile: {
            ...updatedProfile.personalProfile,
            occupation: null,
            revision: 5,
          },
        }),
      changeHealthProfile: jest.fn().mockResolvedValue({ status: 'applied' }),
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(<HealthOverviewScreen api={api} onClose={jest.fn()} />);
    });
    act(() =>
      tree!.root.findByProps({ accessibilityLabel: '编辑身高' }).props.onPress(),
    );
    act(() => {
      tree!.root.findByProps({ accessibilityLabel: '输入身高' }).props.onChangeText(
        '1.69',
      );
      tree!.root.findByProps({ accessibilityLabel: '身高单位米' }).props.onPress();
    });
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '保存' }).props.onPress();
    });

    expect(api.changeHealthProfile).toHaveBeenNthCalledWith(1, {
      clientActionId: '019b9999-9999-7999-8999-999999999999',
      expectedRevision: 3,
      targetType: 'personal_profile',
      operation: 'set',
      fieldName: 'height_cm',
      value: '1.69',
      unit: 'm',
    });
    expect(tree!.root.findByProps({ children: '169 cm' })).toBeTruthy();

    act(() =>
      tree!.root.findByProps({ accessibilityLabel: '清除职业' }).props.onPress(),
    );
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '确认清除' }).props.onPress();
    });
    expect(api.changeHealthProfile).toHaveBeenNthCalledWith(2, {
      clientActionId: '019b9999-9999-7999-8999-999999999999',
      expectedRevision: 4,
      targetType: 'personal_profile',
      operation: 'clear',
      fieldName: 'occupation',
    });
    act(() => tree!.unmount());
  });

  test('adds, edits and retracts health facts without changing their category', async () => {
    const allergy: HealthProfile['healthFacts'][number] = {
      id: '019b8888-8888-7888-8888-888888888888',
      factType: 'allergy',
      statement: '对青霉素过敏',
      assertion: 'present',
      temporalStatus: 'current',
      effectiveStart: null,
      effectiveEnd: null,
      revision: 1,
      createdAt: '2026-08-01T10:00:00+08:00',
      updatedAt: '2026-08-01T10:00:00+08:00',
    };
    const withAllergy = { ...emptyProfile, healthFacts: [allergy] };
    const editedAllergy = {
      ...withAllergy,
      healthFacts: [{ ...allergy, statement: '曾对青霉素过敏', revision: 2 }],
    };
    const api = {
      getHealthProfile: jest
        .fn()
        .mockResolvedValueOnce(emptyProfile)
        .mockResolvedValueOnce(withAllergy)
        .mockResolvedValueOnce(editedAllergy)
        .mockResolvedValueOnce(emptyProfile),
      changeHealthProfile: jest.fn().mockResolvedValue({ status: 'applied' }),
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(<HealthOverviewScreen api={api} onClose={jest.fn()} />);
    });
    act(() =>
      tree!.root.findByProps({ accessibilityLabel: '新增健康情况' }).props.onPress(),
    );
    act(() => {
      tree!.root.findByProps({ accessibilityLabel: '过敏情况' }).props.onPress();
      tree!.root.findByProps({ accessibilityLabel: '输入健康情况' }).props.onChangeText(
        '对青霉素过敏',
      );
    });
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '保存' }).props.onPress();
    });
    expect(api.changeHealthProfile).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        expectedRevision: null,
        operation: 'add',
        factType: 'allergy',
        statement: '对青霉素过敏',
      }),
    );

    act(() =>
      tree!.root
        .findByProps({ accessibilityLabel: '编辑健康情况对青霉素过敏' })
        .props.onPress(),
    );
    expect(
      tree!.root.findAllByProps({ children: '过敏情况' }).length,
    ).toBeGreaterThan(0);
    act(() =>
      tree!.root.findByProps({ accessibilityLabel: '输入健康情况' }).props.onChangeText(
        '曾对青霉素过敏',
      ),
    );
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '保存' }).props.onPress();
    });
    expect(api.changeHealthProfile).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        expectedRevision: 1,
        operation: 'update',
        targetId: allergy.id,
        factType: 'allergy',
      }),
    );

    act(() =>
      tree!.root
        .findByProps({ accessibilityLabel: '删除健康情况曾对青霉素过敏' })
        .props.onPress(),
    );
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '确认删除' }).props.onPress();
    });
    expect(api.changeHealthProfile).toHaveBeenNthCalledWith(3, {
      clientActionId: '019b9999-9999-7999-8999-999999999999',
      expectedRevision: 2,
      targetType: 'health_fact',
      operation: 'retract',
      targetId: allergy.id,
      factType: 'allergy',
    });
    act(() => tree!.unmount());
  });

  test('refreshes the profile and explains a revision conflict', async () => {
    const latest: HealthProfile = {
      ...emptyProfile,
      personalProfile: {
        ...emptyProfile.personalProfile,
        sex: '女',
        revision: 1,
      },
    };
    const saved: HealthProfile = {
      ...latest,
      personalProfile: {
        ...latest.personalProfile,
        sex: '女性',
        revision: 2,
      },
    };
    const api = {
      getHealthProfile: jest
        .fn()
        .mockResolvedValueOnce(emptyProfile)
        .mockResolvedValueOnce(latest)
        .mockResolvedValueOnce(saved),
      changeHealthProfile: jest
        .fn()
        .mockRejectedValueOnce(
          new ApiError('档案版本冲突', 409, 'health_profile_changed'),
        )
        .mockResolvedValueOnce({ status: 'applied' }),
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(<HealthOverviewScreen api={api} onClose={jest.fn()} />);
    });
    act(() =>
      tree!.root.findByProps({ accessibilityLabel: '填写性别' }).props.onPress(),
    );
    act(() =>
      tree!.root.findByProps({ accessibilityLabel: '输入性别' }).props.onChangeText(
        '女性',
      ),
    );
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '保存' }).props.onPress();
    });

    expect(api.getHealthProfile).toHaveBeenCalledTimes(2);
    expect(
      tree!.root.findByProps({
        children: '档案刚更新，已刷新，请确认后再保存一次。',
      }),
    ).toBeTruthy();
    expect(
      tree!.root.findByProps({ accessibilityLabel: '输入性别' }).props.value,
    ).toBe('女性');

    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '保存' }).props.onPress();
    });
    expect(api.changeHealthProfile).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ expectedRevision: 1, value: '女性' }),
    );
    expect(randomUUIDMock).toHaveBeenCalledTimes(2);
    expect(api.getHealthProfile).toHaveBeenCalledTimes(3);
    expect(tree!.root.findByProps({ children: '女性' })).toBeTruthy();
    act(() => tree!.unmount());
  });

  test('does not post a fact twice when the write succeeded but refresh failed', async () => {
    const allergy: HealthProfile['healthFacts'][number] = {
      id: '019baaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa',
      factType: 'allergy',
      statement: '对青霉素过敏',
      assertion: 'present',
      temporalStatus: 'current',
      effectiveStart: null,
      effectiveEnd: null,
      revision: 1,
      createdAt: '2026-08-09T10:00:00+08:00',
      updatedAt: '2026-08-09T10:00:00+08:00',
    };
    const savedProfile = { ...emptyProfile, healthFacts: [allergy] };
    const api = {
      getHealthProfile: jest
        .fn()
        .mockResolvedValueOnce(emptyProfile)
        .mockRejectedValueOnce(new Error('刷新连接中断'))
        .mockResolvedValueOnce(savedProfile),
      changeHealthProfile: jest.fn().mockResolvedValue({ status: 'applied' }),
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(<HealthOverviewScreen api={api} onClose={jest.fn()} />);
    });
    act(() =>
      tree!.root.findByProps({ accessibilityLabel: '新增健康情况' }).props.onPress(),
    );
    act(() => {
      tree!.root.findByProps({ accessibilityLabel: '过敏情况' }).props.onPress();
      tree!.root.findByProps({ accessibilityLabel: '输入健康情况' }).props.onChangeText(
        '对青霉素过敏',
      );
    });
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '保存' }).props.onPress();
    });

    expect(api.changeHealthProfile).toHaveBeenCalledTimes(1);
    expect(randomUUIDMock).toHaveBeenCalledTimes(1);
    expect(tree!.root.findByProps({ children: '修改已经保存' })).toBeTruthy();
    expect(
      tree!.root.findAll(
        (node) =>
          Array.isArray(node.props.children) &&
          node.props.children.join('') ===
            '最新档案暂时没有加载出来：刷新连接中断',
      ).length,
    ).toBeGreaterThan(0);

    await act(async () => {
      tree!.root
        .findByProps({ accessibilityLabel: '重新加载已保存档案' })
        .props.onPress();
    });
    expect(api.changeHealthProfile).toHaveBeenCalledTimes(1);
    expect(api.getHealthProfile).toHaveBeenCalledTimes(3);
    expect(tree!.root.findByProps({ children: '对青霉素过敏' })).toBeTruthy();
    act(() => tree!.unmount());
  });

  test('reuses the action id when a fact write result is uncertain', async () => {
    const allergy: HealthProfile['healthFacts'][number] = {
      id: '019bbbb0-bbbb-7bbb-8bbb-bbbbbbbbbbbb',
      factType: 'allergy',
      statement: '对花生过敏',
      assertion: 'present',
      temporalStatus: 'current',
      effectiveStart: null,
      effectiveEnd: null,
      revision: 1,
      createdAt: '2026-08-09T10:00:00+08:00',
      updatedAt: '2026-08-09T10:00:00+08:00',
    };
    const changeHealthProfile = jest
      .fn()
      .mockRejectedValueOnce(new Error('提交连接中断'))
      .mockResolvedValueOnce({ status: 'applied' });
    const api = {
      getHealthProfile: jest
        .fn()
        .mockResolvedValueOnce(emptyProfile)
        .mockResolvedValueOnce({ ...emptyProfile, healthFacts: [allergy] }),
      changeHealthProfile,
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(<HealthOverviewScreen api={api} onClose={jest.fn()} />);
    });
    act(() =>
      tree!.root.findByProps({ accessibilityLabel: '新增健康情况' }).props.onPress(),
    );
    act(() => {
      tree!.root.findByProps({ accessibilityLabel: '过敏情况' }).props.onPress();
      tree!.root.findByProps({ accessibilityLabel: '输入健康情况' }).props.onChangeText(
        '对花生过敏',
      );
    });
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '保存' }).props.onPress();
    });
    expect(tree!.root.findByProps({ children: '提交连接中断' })).toBeTruthy();

    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '保存' }).props.onPress();
    });

    expect(api.changeHealthProfile).toHaveBeenCalledTimes(2);
    const firstActionId = changeHealthProfile.mock.calls[0][0].clientActionId;
    const secondActionId = changeHealthProfile.mock.calls[1][0].clientActionId;
    expect(secondActionId).toBe(firstActionId);
    expect(randomUUIDMock).toHaveBeenCalledTimes(1);
    expect(tree!.root.findByProps({ children: '对花生过敏' })).toBeTruthy();
    act(() => tree!.unmount());
  });

  test('replays the original pending request after a profile refresh', async () => {
    const refreshed: HealthProfile = {
      ...emptyProfile,
      personalProfile: {
        ...emptyProfile.personalProfile,
        residentArea: '上海',
        revision: 1,
      },
    };
    const saved: HealthProfile = {
      ...refreshed,
      personalProfile: {
        ...refreshed.personalProfile,
        sex: '女性',
        revision: 2,
      },
    };
    const changeHealthProfile = jest
      .fn()
      .mockRejectedValueOnce(new Error('提交结果未知'))
      .mockResolvedValueOnce({ status: 'applied' });
    const api = {
      getHealthProfile: jest
        .fn()
        .mockResolvedValueOnce(emptyProfile)
        .mockResolvedValueOnce(refreshed)
        .mockResolvedValueOnce(saved),
      changeHealthProfile,
    } as unknown as ApiClient;
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(<HealthOverviewScreen api={api} onClose={jest.fn()} />);
    });
    act(() =>
      tree!.root.findByProps({ accessibilityLabel: '填写性别' }).props.onPress(),
    );
    act(() =>
      tree!.root.findByProps({ accessibilityLabel: '输入性别' }).props.onChangeText(
        '女性',
      ),
    );
    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '保存' }).props.onPress();
    });

    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '同步 Health Connect 数据' }).props.onPress();
      await Promise.resolve();
    });
    expect(
      tree!.root.findByProps({ accessibilityLabel: '输入性别' }).props.value,
    ).toBe('女性');

    await act(async () => {
      tree!.root.findByProps({ accessibilityLabel: '保存' }).props.onPress();
    });

    expect(changeHealthProfile).toHaveBeenCalledTimes(2);
    expect(changeHealthProfile.mock.calls[1][0]).toEqual(
      changeHealthProfile.mock.calls[0][0],
    );
    expect(changeHealthProfile.mock.calls[1][0]).toMatchObject({
      clientActionId: '019b9999-9999-7999-8999-999999999999',
      expectedRevision: 0,
      fieldName: 'sex',
      value: '女性',
    });
    expect(randomUUIDMock).toHaveBeenCalledTimes(1);
    expect(tree!.root.findByProps({ children: '女性' })).toBeTruthy();
    act(() => tree!.unmount());
  });
});

describe('sourceLabel', () => {
  test('如实区分 Gadgetbridge 直读与 Zepp/Health Connect，未知来源显示包名', () => {
    expect(sourceLabel('com.huami.watch.hmwatchmanager')).toBe('Zepp · Health Connect');
    expect(sourceLabel('nodomain.freeyourgadget.gadgetbridge')).toBe('Gadgetbridge');
    expect(sourceLabel('com.example.other-health-app')).toBe('com.example.other-health-app');
  });
});
