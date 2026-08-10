import * as BackgroundTask from 'expo-background-task';
import * as TaskManager from 'expo-task-manager';
import { Platform } from 'react-native';

import { isBackgroundReadAvailable } from '../../modules/health-connect-background';
import { appClient } from '../api/app-client';
import { errorMessage } from '../api/client';
import { secureTokenStore } from '../auth/token-store';
import { secureHealthConnectTokenStore } from './health-connect-store';
import {
  HealthConnectReadPermissionError,
  nativeHealthConnect,
  prepareHealthConnect,
} from './native-health-connect';
import {
  HealthConnectTokenExpiredError,
  runHealthConnectSync,
} from './sync-health-connect';

export const BACKGROUND_HEALTH_TASK = 'antang.health-connect.periodic-sync';

export type BackgroundHealthSyncStatus =
  | 'not_ready'
  | 'disabled'
  | 'enabled'
  | 'needs_foreground'
  | 'unavailable'
  | 'permission_missing';

export type BackgroundHealthSyncInfo = {
  status: BackgroundHealthSyncStatus;
  error: string | null;
};

export async function runBackgroundHealthSync(): Promise<'skipped' | 'synced'> {
  if (Platform.OS !== 'android') return 'skipped';
  if ((await secureTokenStore.load()) === null) return 'skipped';

  const userId = await secureHealthConnectTokenStore.getActiveUserId();
  if (userId === null) return 'skipped';
  const state = await secureHealthConnectTokenStore.loadBackgroundState(userId);
  if (
    !state.initialSyncComplete ||
    !state.backgroundEnabled ||
    state.needsForeground
  ) {
    return 'skipped';
  }

  try {
    const sessionSignal = appClient.getSessionSignal();
    const currentUser = await appClient.me(sessionSignal);
    if (currentUser.id !== userId) return 'skipped';
    await prepareHealthConnect(nativeHealthConnect, false);
    if (!(await isBackgroundReadAvailable())) return 'skipped';
    if (!(await nativeHealthConnect.hasBackgroundPermission())) return 'skipped';

    await runHealthConnectSync(
      userId,
      nativeHealthConnect,
      secureHealthConnectTokenStore,
      appClient,
      undefined,
      new Date(),
      sessionSignal,
    );
    await secureHealthConnectTokenStore.recordBackgroundSuccess(
      userId,
      new Date().toISOString(),
    );
    return 'synced';
  } catch (error) {
    await secureHealthConnectTokenStore.recordBackgroundFailure(
      userId,
      errorMessage(error),
      error instanceof HealthConnectTokenExpiredError,
      new Date().toISOString(),
    );
    throw error;
  }
}

export async function getBackgroundHealthSyncInfo(
  userId: string,
): Promise<BackgroundHealthSyncInfo> {
  if (Platform.OS !== 'android') {
    return { status: 'unavailable', error: null };
  }
  const activeUserId = await secureHealthConnectTokenStore.getActiveUserId();
  const state = await secureHealthConnectTokenStore.loadBackgroundState(userId);
  if (activeUserId !== userId || !state.initialSyncComplete) {
    return { status: 'not_ready', error: state.lastError };
  }
  const registered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_HEALTH_TASK);
  if (!state.backgroundEnabled || !registered) {
    return { status: 'disabled', error: state.lastError };
  }
  if (state.needsForeground) {
    return { status: 'needs_foreground', error: state.lastError };
  }
  try {
    await prepareHealthConnect(nativeHealthConnect, false);
  } catch (error) {
    return {
      status:
        error instanceof HealthConnectReadPermissionError
          ? 'permission_missing'
          : 'unavailable',
      error: errorMessage(error),
    };
  }
  if (!(await isBackgroundReadAvailable())) {
    return { status: 'unavailable', error: state.lastError };
  }
  if (!(await nativeHealthConnect.hasBackgroundPermission())) {
    return { status: 'permission_missing', error: state.lastError };
  }
  return { status: 'enabled', error: state.lastError };
}

export async function enableBackgroundHealthSync(userId: string): Promise<void> {
  if (Platform.OS !== 'android') {
    throw new Error('Health Connect 后台周期检查只支持 Android');
  }
  const activeUserId = await secureHealthConnectTokenStore.getActiveUserId();
  const state = await secureHealthConnectTokenStore.loadBackgroundState(userId);
  if (activeUserId !== userId || !state.initialSyncComplete) {
    throw new Error('请先连接 Health Connect，并完成一次前台手环同步');
  }
  await prepareHealthConnect(nativeHealthConnect, false);
  if (!(await isBackgroundReadAvailable())) {
    throw new Error('这台设备不支持 Health Connect 后台读取');
  }
  if (!(await nativeHealthConnect.requestBackgroundPermission())) {
    throw new Error('没有获得 Health Connect 后台读取权限');
  }

  await BackgroundTask.registerTaskAsync(BACKGROUND_HEALTH_TASK, {
    minimumInterval: 15,
  });
  await secureHealthConnectTokenStore.enableBackground(userId);
}

export async function disableBackgroundHealthSync(userId: string): Promise<void> {
  await BackgroundTask.unregisterTaskAsync(BACKGROUND_HEALTH_TASK);
  await secureHealthConnectTokenStore.disableBackground(userId);
}

TaskManager.defineTask(BACKGROUND_HEALTH_TASK, async () => {
  try {
    await runBackgroundHealthSync();
    return BackgroundTask.BackgroundTaskResult.Success;
  } catch {
    return BackgroundTask.BackgroundTaskResult.Failed;
  }
});
