import { act, create } from 'react-test-renderer';
import { Modal, Pressable, Text, View } from 'react-native';

import type { ApiClient } from '../api/client';
import type { WearableDashboard, WearableDashboardSource } from '../api/types';
import { HealthDashboardScreen } from './HealthDashboardScreen';

jest.mock('react-native-chart-kit', () => ({ LineChart: () => null }));
void [Modal, Pressable, Text, View];

function source(sourcePackage: string, steps: number): WearableDashboardSource {
  return {
    sourcePackage,
    device: { manufacturer: 'Amazfit', model: 'Active 2' },
    latestObservedAt: '2026-09-26T12:00:00Z',
    daily: [{ day: '2026-09-26', steps, distanceMeters: 0 }],
    heartRateTrend: [
      { observedAt: '2026-09-26T11:00:00Z', beatsPerMinute: 75 },
      { observedAt: '2026-09-26T12:00:00Z', beatsPerMinute: 80 },
    ],
    sleepSessions: [],
    totals: {
      heartRateSamples: 2,
      heartRateMinimum: 75,
      heartRateMaximum: 80,
      heartRateAverage: 77.5,
      steps,
      distanceMeters: 0,
      oxygenSaturationSamples: 0,
      oxygenSaturationMinimum: null,
      oxygenSaturationAverage: null,
      restingHeartRateSamples: 0,
      restingHeartRateMinimum: null,
      sleepSessions: 0,
      sleepMinutes: 0,
    },
  };
}

test('shows saved server history and separates Zepp from Gadgetbridge', async () => {
  const dashboard: WearableDashboard = {
    periodStart: '2026-08-27T12:00:00Z',
    periodEnd: '2026-09-26T12:00:00Z',
    sources: [
      source('com.huami.watch.hmwatchmanager', 123),
      source('nodomain.freeyourgadget.gadgetbridge', 456),
    ],
  };
  const api = {
    getWearableDashboard: jest.fn(async () => dashboard),
  } as unknown as ApiClient;
  let tree: ReturnType<typeof create>;

  await act(async () => {
    tree = create(
      <HealthDashboardScreen api={api} importPreview={null} onClose={jest.fn()} refreshKey="" visible />,
    );
  });

  expect(api.getWearableDashboard).toHaveBeenCalledWith(expect.any(AbortSignal));
  expect(tree!.root.findByProps({ children: '健康看板' })).toBeTruthy();
  expect(tree!.root.findByProps({ children: '123' })).toBeTruthy();
  act(() => {
    tree!.root.findByProps({ accessibilityLabel: '查看Gadgetbridge数据' }).props.onPress();
  });
  expect(tree!.root.findByProps({ children: '456' })).toBeTruthy();
  act(() => tree!.unmount());
});
