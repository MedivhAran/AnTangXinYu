import { act, create } from 'react-test-renderer';
import { Platform } from 'react-native';

import {
  healthConnectReadPermissions,
  type HealthConnectGateway,
} from './health-connect-types';
import type { HealthConnectStore } from './health-connect-store';
import { runHealthConnectSync } from './sync-health-connect';
import { useHealthConnect } from './use-health-connect';

jest.mock('./background-health-sync', () => ({
  getBackgroundHealthSyncInfo: jest.fn().mockResolvedValue({
    status: 'not_ready',
    error: null,
  }),
  enableBackgroundHealthSync: jest.fn(),
  disableBackgroundHealthSync: jest.fn(),
}));
jest.mock('./sync-health-connect', () => ({
  runHealthConnectSync: jest.fn(),
}));

describe('useHealthConnect', () => {
  test('two immediate sync calls share the real controller so stop still cancels it', async () => {
    Object.defineProperty(Platform, 'OS', { configurable: true, value: 'android' });
    let markSyncStarted!: () => void;
    let finishSync!: () => void;
    let actualSignal!: AbortSignal;
    const syncStarted = new Promise<void>((resolve) => {
      markSyncStarted = resolve;
    });
    jest.mocked(runHealthConnectSync).mockImplementation(
      async (_userId, _gateway, _store, _importer, _progress, _now, signal) => {
        if (signal === undefined) throw new Error('missing abort signal');
        actualSignal = signal;
        markSyncStarted();
        await new Promise<void>((resolve) => {
          finishSync = resolve;
        });
      },
    );
    const gateway = {
      getSdkStatus: jest.fn(async () => 3),
      initialize: jest.fn(async () => true),
      requestReadPermissions: jest.fn(),
      getGrantedPermissions: jest.fn(async () => healthConnectReadPermissions),
      readRecords: jest.fn(),
      getChanges: jest.fn(),
    } as unknown as HealthConnectGateway;
    const tokenStore = {
      getActiveUserId: jest.fn(async () => 'another-user'),
      setActiveUserId: jest.fn(),
      loadTokens: jest.fn(),
      saveToken: jest.fn(),
      loadBackgroundState: jest.fn(),
      markInitialSyncComplete: jest.fn(),
      enableBackground: jest.fn(),
      disableBackground: jest.fn(),
      recordBackgroundSuccess: jest.fn(),
      recordBackgroundFailure: jest.fn(),
    } as unknown as HealthConnectStore;
    const importer = { importWearableRecords: jest.fn() };
    let current!: ReturnType<typeof useHealthConnect>;

    function Probe() {
      current = useHealthConnect('user-1', importer, gateway, tokenStore);
      return null;
    }

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(<Probe />);
    });

    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => {
      first = current.sync();
      second = current.sync();
    });
    await syncStarted;
    expect(second).toBe(first);

    act(() => current.stop());
    expect(actualSignal.aborted).toBe(true);
    act(() => tree!.unmount());
    finishSync();
    await first;

    expect(runHealthConnectSync).toHaveBeenCalledTimes(1);
    expect(importer.importWearableRecords).not.toHaveBeenCalled();
  });
});
