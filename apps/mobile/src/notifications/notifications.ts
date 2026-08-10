import Constants from 'expo-constants';
import { randomUUID } from 'expo-crypto';
import * as Notifications from 'expo-notifications';
import * as SecureStore from 'expo-secure-store';

import type { ApiClient } from '../api/client';

const INSTALLATION_ID_KEY = 'antang.notifications.installation-id';
const PUSH_REGISTRATION_TIMEOUT_MS = 15_000;
let installationIdRequest: Promise<string> | null = null;
let installationSyncTail: Promise<void> = Promise.resolve();

export type PushPermission = 'undetermined' | 'denied' | 'granted';
type InstallationApi = Pick<ApiClient, 'syncPushInstallation'>;
type PermissionObserved = (permission: PushPermission) => void;

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: false,
    shouldShowList: false,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

function permissionStatus(status: string): PushPermission {
  if (status === 'granted' || status === 'denied' || status === 'undetermined') {
    return status;
  }
  throw new Error(`未知通知权限状态：${status}`);
}

function installationId(): Promise<string> {
  if (installationIdRequest !== null) return installationIdRequest;
  const request = (async () => {
    const existing = await SecureStore.getItemAsync(INSTALLATION_ID_KEY);
    if (existing !== null) return existing;
    const created = randomUUID();
    await SecureStore.setItemAsync(INSTALLATION_ID_KEY, created);
    return created;
  })().finally(() => {
    if (installationIdRequest === request) installationIdRequest = null;
  });
  installationIdRequest = request;
  return request;
}

function serializeInstallationSync<T>(operation: () => Promise<T>): Promise<T> {
  const request = installationSyncTail.then(operation);
  installationSyncTail = request.then(
    () => undefined,
    () => undefined,
  );
  return request;
}

function appVersion(): string {
  const version = Constants.expoConfig?.version;
  if (!version) throw new Error('App 配置缺少 version');
  return version;
}

function projectId(): string {
  const id =
    Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
  if (!id) throw new Error('App 配置缺少 EAS projectId');
  return id;
}

async function expoPushToken(
  options: Parameters<typeof Notifications.getExpoPushTokenAsync>[0],
): Promise<string> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    const result = await Promise.race([
      Notifications.getExpoPushTokenAsync(options),
      new Promise<never>((_, reject) => {
        timeout = setTimeout(
          () =>
            reject(
              new Error(
                '通知注册超时，请检查网络和 Google Play 服务后重试。',
              ),
            ),
          PUSH_REGISTRATION_TIMEOUT_MS,
        );
      }),
    ]);
    return result.data;
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
  }
}

async function createChannels(): Promise<void> {
  await Notifications.setNotificationChannelAsync('care-checkins-v1', {
    name: '日常与计划关怀',
    importance: Notifications.AndroidImportance.DEFAULT,
  });
  await Notifications.setNotificationChannelAsync('health-care-v1', {
    name: '健康关怀',
    importance: Notifications.AndroidImportance.HIGH,
  });
}

async function syncInstallation(
  api: InstallationApi,
  requestPermission: boolean,
  onPermission?: PermissionObserved,
): Promise<PushPermission> {
  let permission = permissionStatus(
    (await Notifications.getPermissionsAsync()).status,
  );
  let channelsCreated = false;
  if (requestPermission && permission === 'undetermined') {
    await createChannels();
    channelsCreated = true;
    permission = permissionStatus(
      (await Notifications.requestPermissionsAsync()).status,
    );
  }
  onPermission?.(permission);

  let token: string | null = null;
  if (permission === 'granted') {
    if (!channelsCreated) await createChannels();
    token = await expoPushToken({ projectId: projectId() });
  }
  await api.syncPushInstallation(
    await installationId(),
    token,
    permission,
    appVersion(),
  );
  return permission;
}

export async function syncCurrentNotificationState(
  api: InstallationApi,
  onPermission?: PermissionObserved,
): Promise<PushPermission> {
  return serializeInstallationSync(() =>
    syncInstallation(api, false, onPermission),
  );
}

export async function requestAndSync(
  api: InstallationApi,
  onPermission?: PermissionObserved,
): Promise<PushPermission> {
  return serializeInstallationSync(() =>
    syncInstallation(api, true, onPermission),
  );
}

export function addPushTokenSyncListener(
  api: InstallationApi,
  onSuccess: () => void,
  onError: (error: unknown) => void,
): Notifications.EventSubscription {
  return Notifications.addPushTokenListener(async (devicePushToken) => {
    try {
      await serializeInstallationSync(async () => {
        const permission = permissionStatus(
          (await Notifications.getPermissionsAsync()).status,
        );
        const token =
          permission === 'granted'
            ? await expoPushToken({
                projectId: projectId(),
                devicePushToken,
              })
            : null;
        await api.syncPushInstallation(
          await installationId(),
          token,
          permission,
          appVersion(),
        );
      });
      onSuccess();
    } catch (error) {
      onError(error);
    }
  });
}
