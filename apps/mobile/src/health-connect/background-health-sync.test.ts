import * as BackgroundTask from 'expo-background-task';
import * as TaskManager from 'expo-task-manager';
import { Platform } from 'react-native';
import { requestPermission } from 'react-native-health-connect';

import { appClient } from '../api/app-client';
import { secureTokenStore } from '../auth/token-store';
import {
  BACKGROUND_HEALTH_TASK,
  disableBackgroundHealthSync,
  enableBackgroundHealthSync,
  getBackgroundHealthSyncInfo,
  runBackgroundHealthSync,
} from './background-health-sync';
import { isBackgroundReadAvailable } from '../../modules/health-connect-background';
import { secureHealthConnectTokenStore } from './health-connect-store';
import { nativeHealthConnect } from './native-health-connect';
import { healthConnectReadPermissions } from './health-connect-types';
import {
  HealthConnectTokenExpiredError,
  runHealthConnectSync,
} from './sync-health-connect';

jest.mock('expo-background-task', () => ({
  BackgroundTaskResult: { Success: 'success', Failed: 'failed' },
  registerTaskAsync: jest.fn(),
  unregisterTaskAsync: jest.fn(),
}));
jest.mock('expo-task-manager', () => ({
  defineTask: jest.fn(),
  isTaskRegisteredAsync: jest.fn(),
}));
jest.mock('react-native-health-connect', () => ({
  requestPermission: jest.fn(),
  getGrantedPermissions: jest.fn(),
  getSdkStatus: jest.fn(),
  initialize: jest.fn(),
  readRecords: jest.fn(),
  getChanges: jest.fn(),
}));
jest.mock('../../modules/health-connect-background', () => ({
  isBackgroundReadAvailable: jest.fn(),
}));
jest.mock('../api/app-client', () => ({
  appClient: {
    getSessionSignal: jest.fn(),
    me: jest.fn(),
    importWearableRecords: jest.fn(),
  },
}));
jest.mock('../auth/token-store', () => ({
  secureTokenStore: { load: jest.fn() },
}));
jest.mock('./health-connect-store', () => ({
  secureHealthConnectTokenStore: {
    getActiveUserId: jest.fn(),
    loadBackgroundState: jest.fn(),
    enableBackground: jest.fn(),
    disableBackground: jest.fn(),
    recordBackgroundSuccess: jest.fn(),
    recordBackgroundFailure: jest.fn(),
  },
}));
jest.mock('./native-health-connect', () => ({
  ...jest.requireActual('./native-health-connect'),
  nativeHealthConnect: {
    getSdkStatus: jest.fn(),
    initialize: jest.fn(),
    requestReadPermissions: jest.fn(),
    getGrantedPermissions: jest.fn(),
    hasBackgroundPermission: jest.fn(),
    requestBackgroundPermission: jest.fn(),
  },
}));
jest.mock('./sync-health-connect', () => {
  const actual = jest.requireActual('./sync-health-connect');
  return { ...actual, runHealthConnectSync: jest.fn() };
});

const userId = '019b1111-1111-7111-8111-111111111111';
const readyState = {
  initialSyncComplete: true,
  backgroundEnabled: true,
  needsForeground: false,
  lastRunAt: null,
  lastError: null,
};
const definedTask = jest.mocked(TaskManager.defineTask).mock.calls[0];

describe('background Health Connect sync', () => {
  let sessionController: AbortController;

  beforeEach(() => {
    jest.clearAllMocks();
    sessionController = new AbortController();
    Object.defineProperty(Platform, 'OS', { configurable: true, value: 'android' });
    jest.mocked(secureTokenStore.load).mockResolvedValue({
      accessToken: 'access',
      refreshToken: 'refresh',
      expiresIn: 900,
    });
    jest.mocked(secureHealthConnectTokenStore.getActiveUserId).mockResolvedValue(userId);
    jest.mocked(secureHealthConnectTokenStore.loadBackgroundState).mockResolvedValue(
      readyState,
    );
    jest.mocked(appClient.me).mockResolvedValue({ id: userId, username: 'alice' });
    jest.mocked(appClient.getSessionSignal).mockReturnValue(
      sessionController.signal,
    );
    jest.mocked(isBackgroundReadAvailable).mockResolvedValue(true);
    jest.mocked(nativeHealthConnect.getSdkStatus).mockResolvedValue(3);
    jest.mocked(nativeHealthConnect.initialize).mockResolvedValue(true);
    jest.mocked(nativeHealthConnect.getGrantedPermissions).mockResolvedValue(
      healthConnectReadPermissions,
    );
    jest.mocked(nativeHealthConnect.hasBackgroundPermission).mockResolvedValue(true);
    jest.mocked(runHealthConnectSync).mockResolvedValue(undefined);
    jest.mocked(TaskManager.isTaskRegisteredAsync).mockResolvedValue(true);
  });

  test('defines the Expo task at module load', () => {
    expect(definedTask).toEqual([
      BACKGROUND_HEALTH_TASK,
      expect.any(Function),
    ]);
  });

  test('skips before native access when there is no current login', async () => {
    jest.mocked(secureTokenStore.load).mockResolvedValue(null);

    await expect(runBackgroundHealthSync()).resolves.toBe('skipped');

    expect(appClient.me).not.toHaveBeenCalled();
    expect(isBackgroundReadAvailable).not.toHaveBeenCalled();
    expect(runHealthConnectSync).not.toHaveBeenCalled();
  });

  test('does not sync when any account, foreground, feature or permission guard fails', async () => {
    jest.mocked(secureHealthConnectTokenStore.loadBackgroundState).mockResolvedValueOnce({
      ...readyState,
      initialSyncComplete: false,
    });
    await expect(runBackgroundHealthSync()).resolves.toBe('skipped');

    jest.mocked(appClient.me).mockResolvedValueOnce({
      id: '019b2222-2222-7222-8222-222222222222',
      username: 'bob',
    });
    await expect(runBackgroundHealthSync()).resolves.toBe('skipped');

    jest.mocked(isBackgroundReadAvailable).mockResolvedValueOnce(false);
    await expect(runBackgroundHealthSync()).resolves.toBe('skipped');

    jest.mocked(nativeHealthConnect.hasBackgroundPermission).mockResolvedValueOnce(false);
    await expect(runBackgroundHealthSync()).resolves.toBe('skipped');

    expect(runHealthConnectSync).not.toHaveBeenCalled();
  });

  test('syncs only after account, foreground, feature and permission guards pass', async () => {
    const events: string[] = [];
    jest.mocked(nativeHealthConnect.getSdkStatus).mockImplementation(async () => {
      events.push('sdk');
      return 3;
    });
    jest.mocked(nativeHealthConnect.initialize).mockImplementation(async () => {
      events.push('initialize');
      return true;
    });
    jest.mocked(nativeHealthConnect.getGrantedPermissions).mockImplementation(
      async () => {
        events.push('base-permissions');
        return healthConnectReadPermissions;
      },
    );
    jest.mocked(isBackgroundReadAvailable).mockImplementation(async () => {
      events.push('feature');
      return true;
    });
    jest.mocked(nativeHealthConnect.hasBackgroundPermission).mockImplementation(
      async () => {
        events.push('background-permission');
        return true;
      },
    );
    jest.mocked(runHealthConnectSync).mockImplementation(async () => {
      events.push('sync');
    });

    await expect(runBackgroundHealthSync()).resolves.toBe('synced');

    expect(runHealthConnectSync).toHaveBeenCalledWith(
      userId,
      nativeHealthConnect,
      secureHealthConnectTokenStore,
      appClient,
      undefined,
      expect.any(Date),
      sessionController.signal,
      false,
    );
    expect(secureHealthConnectTokenStore.recordBackgroundSuccess).toHaveBeenCalledWith(
      userId,
      expect.any(String),
    );
    expect(events).toEqual([
      'sdk',
      'initialize',
      'base-permissions',
      'feature',
      'background-permission',
      'sync',
    ]);
  });

  test('binds the account check and all uploads to the session that started the task', async () => {
    await expect(runBackgroundHealthSync()).resolves.toBe('synced');

    expect(appClient.me).toHaveBeenCalledWith(sessionController.signal);
    expect(runHealthConnectSync).toHaveBeenCalledWith(
      userId,
      nativeHealthConnect,
      secureHealthConnectTokenStore,
      appClient,
      undefined,
      expect.any(Date),
      sessionController.signal,
      false,
    );
  });

  test('marks an expired cursor for foreground handling and fails the run', async () => {
    jest.mocked(runHealthConnectSync).mockRejectedValue(
      new HealthConnectTokenExpiredError('HeartRate'),
    );

    await expect(runBackgroundHealthSync()).rejects.toThrow('游标已失效');

    expect(secureHealthConnectTokenStore.recordBackgroundFailure).toHaveBeenCalledWith(
      userId,
      expect.stringContaining('游标已失效'),
      true,
      expect.any(String),
    );
  });

  test('reports a task failure to Expo when a sync fails', async () => {
    jest.mocked(runHealthConnectSync).mockRejectedValue(new Error('server down'));
    const executor = definedTask?.[1];
    expect(executor).toBeDefined();

    await expect(
      executor!({
        data: undefined,
        error: null,
        executionInfo: {
          eventId: 'event-1',
          taskName: BACKGROUND_HEALTH_TASK,
        },
      }),
    ).resolves.toBe(BackgroundTask.BackgroundTaskResult.Failed);
    expect(secureHealthConnectTokenStore.recordBackgroundFailure).toHaveBeenCalledWith(
      userId,
      'server down',
      false,
      expect.any(String),
    );
  });

  test('does not ask for background permission while merely reading status', async () => {
    await expect(getBackgroundHealthSyncInfo(userId)).resolves.toEqual({
      status: 'enabled',
      error: null,
    });
    expect(requestPermission).not.toHaveBeenCalled();
    expect(nativeHealthConnect.requestBackgroundPermission).not.toHaveBeenCalled();
  });

  test('reports a revoked foreground read permission without probing native features first', async () => {
    jest.mocked(nativeHealthConnect.getGrantedPermissions).mockResolvedValue(
      healthConnectReadPermissions.slice(0, -1),
    );

    await expect(getBackgroundHealthSyncInfo(userId)).resolves.toEqual({
      status: 'permission_missing',
      error: '没有获得全部十类 Health Connect 读取权限',
    });

    expect(isBackgroundReadAvailable).not.toHaveBeenCalled();
  });

  test('reports disabled after the user turns off a task that had an expired cursor', async () => {
    jest.mocked(secureHealthConnectTokenStore.loadBackgroundState).mockResolvedValue({
      ...readyState,
      backgroundEnabled: false,
      needsForeground: true,
      lastError: '游标失效',
    });
    jest.mocked(TaskManager.isTaskRegisteredAsync).mockResolvedValue(false);

    await expect(getBackgroundHealthSyncInfo(userId)).resolves.toEqual({
      status: 'disabled',
      error: '游标失效',
    });
  });

  test('asks and registers at 15 minutes only after the explicit enable action', async () => {
    jest.mocked(nativeHealthConnect.requestBackgroundPermission).mockResolvedValue(true);

    await enableBackgroundHealthSync(userId);

    expect(nativeHealthConnect.requestBackgroundPermission).toHaveBeenCalledTimes(1);
    expect(BackgroundTask.registerTaskAsync).toHaveBeenCalledWith(
      BACKGROUND_HEALTH_TASK,
      { minimumInterval: 15 },
    );
    expect(secureHealthConnectTokenStore.enableBackground).toHaveBeenCalledWith(userId);
  });

  test('unregisters before persisting that background checks are disabled', async () => {
    await disableBackgroundHealthSync(userId);

    expect(BackgroundTask.unregisterTaskAsync).toHaveBeenCalledWith(
      BACKGROUND_HEALTH_TASK,
    );
    expect(secureHealthConnectTokenStore.disableBackground).toHaveBeenCalledWith(userId);
    expect(
      jest.mocked(BackgroundTask.unregisterTaskAsync).mock.invocationCallOrder[0],
    ).toBeLessThan(
      jest.mocked(secureHealthConnectTokenStore.disableBackground).mock
        .invocationCallOrder[0],
    );
  });
});
