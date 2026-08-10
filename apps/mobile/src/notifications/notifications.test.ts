import * as ExpoNotifications from 'expo-notifications';
import * as Crypto from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';

import {
  addPushTokenSyncListener,
  requestAndSync,
  syncCurrentNotificationState,
} from './notifications';

jest.mock('expo-notifications', () => ({
  AndroidImportance: { DEFAULT: 3, HIGH: 4 },
  PermissionStatus: {
    UNDETERMINED: 'undetermined',
    DENIED: 'denied',
    GRANTED: 'granted',
  },
  setNotificationHandler: jest.fn(),
  setNotificationChannelAsync: jest.fn(),
  getPermissionsAsync: jest.fn(),
  requestPermissionsAsync: jest.fn(),
  getExpoPushTokenAsync: jest.fn(),
  addPushTokenListener: jest.fn(),
}));

jest.mock('expo-constants', () => ({
  __esModule: true,
  default: {
    expoConfig: {
      version: '1.0.0',
      extra: { eas: { projectId: 'd04128ba-7ddd-4df4-a952-d52a8a9bea68' } },
    },
  },
}));

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
}));
jest.mock('expo-crypto', () => ({ randomUUID: jest.fn() }));

const mockSetNotificationHandler = jest.mocked(
  ExpoNotifications.setNotificationHandler,
);
const mockSetNotificationChannelAsync = jest.mocked(
  ExpoNotifications.setNotificationChannelAsync,
);
const mockGetPermissionsAsync = jest.mocked(
  ExpoNotifications.getPermissionsAsync,
);
const mockRequestPermissionsAsync = jest.mocked(
  ExpoNotifications.requestPermissionsAsync,
);
const mockGetExpoPushTokenAsync = jest.mocked(
  ExpoNotifications.getExpoPushTokenAsync,
);
const mockAddPushTokenListener = jest.mocked(
  ExpoNotifications.addPushTokenListener,
);
const mockGetItemAsync = jest.mocked(SecureStore.getItemAsync);
const mockSetItemAsync = jest.mocked(SecureStore.setItemAsync);
const mockRandomUUID = jest.mocked(Crypto.randomUUID);

describe('notifications', () => {
  beforeEach(() => {
    mockSetNotificationChannelAsync.mockReset();
    mockGetPermissionsAsync.mockReset();
    mockRequestPermissionsAsync.mockReset();
    mockGetExpoPushTokenAsync.mockReset();
    mockAddPushTokenListener.mockReset();
    mockGetItemAsync.mockReset();
    mockSetItemAsync.mockReset();
    mockRandomUUID
      .mockReset()
      .mockReturnValue('019b8888-8888-7888-8888-888888888888');
  });

  test('suppresses a duplicate system notification while the app is foregrounded', async () => {
    expect(mockSetNotificationHandler).toHaveBeenCalledTimes(1);
    const handler = mockSetNotificationHandler.mock.calls[0][0];
    expect(handler).not.toBeNull();

    await expect(handler!.handleNotification({} as never)).resolves.toEqual({
      shouldShowBanner: false,
      shouldShowList: false,
      shouldPlaySound: false,
      shouldSetBadge: false,
    });
  });

  test('syncs an undetermined permission without prompting or fetching a token', async () => {
    mockGetPermissionsAsync.mockResolvedValue({
      status: ExpoNotifications.PermissionStatus.UNDETERMINED,
    } as never);
    mockGetItemAsync.mockResolvedValue(null);
    mockSetItemAsync.mockResolvedValue(undefined);
    const api = { syncPushInstallation: jest.fn().mockResolvedValue(undefined) };

    await expect(syncCurrentNotificationState(api)).resolves.toBe('undetermined');

    expect(mockRequestPermissionsAsync).not.toHaveBeenCalled();
    expect(mockSetNotificationChannelAsync).not.toHaveBeenCalled();
    expect(mockGetExpoPushTokenAsync).not.toHaveBeenCalled();
    expect(api.syncPushInstallation).toHaveBeenCalledWith(
      '019b8888-8888-7888-8888-888888888888',
      null,
      'undetermined',
      '1.0.0',
    );
    expect(mockSetItemAsync).toHaveBeenCalledWith(
      'antang.notifications.installation-id',
      '019b8888-8888-7888-8888-888888888888',
    );
  });

  test('requests permission only on demand and registers the granted installation', async () => {
    mockGetPermissionsAsync.mockResolvedValue({
      status: ExpoNotifications.PermissionStatus.UNDETERMINED,
    } as never);
    mockRequestPermissionsAsync.mockResolvedValue({
      status: ExpoNotifications.PermissionStatus.GRANTED,
    } as never);
    mockGetExpoPushTokenAsync.mockResolvedValue({
      type: 'expo',
      data: 'ExponentPushToken[test]',
    });
    mockGetItemAsync.mockResolvedValue(
      '019b9999-9999-7999-8999-999999999999',
    );
    const api = { syncPushInstallation: jest.fn().mockResolvedValue(undefined) };

    await expect(requestAndSync(api)).resolves.toBe('granted');

    expect(mockSetNotificationChannelAsync.mock.calls.map((call) => call[0])).toEqual([
      'care-checkins-v1',
      'health-care-v1',
    ]);
    expect(mockRequestPermissionsAsync).toHaveBeenCalledTimes(1);
    expect(mockGetExpoPushTokenAsync).toHaveBeenCalledWith({
      projectId: 'd04128ba-7ddd-4df4-a952-d52a8a9bea68',
    });
    expect(api.syncPushInstallation).toHaveBeenCalledWith(
      '019b9999-9999-7999-8999-999999999999',
      'ExponentPushToken[test]',
      'granted',
      '1.0.0',
    );
  });

  test('re-registers the same installation when the native push token changes', async () => {
    let tokenListener: ((token: { type: 'android'; data: string }) => Promise<void>) | null =
      null;
    const subscription = { remove: jest.fn() };
    mockAddPushTokenListener.mockImplementation((listener) => {
      tokenListener = listener as typeof tokenListener;
      return subscription;
    });
    mockGetPermissionsAsync.mockResolvedValue({
      status: ExpoNotifications.PermissionStatus.GRANTED,
    } as never);
    mockGetExpoPushTokenAsync.mockResolvedValue({
      type: 'expo',
      data: 'ExponentPushToken[rotated]',
    });
    mockGetItemAsync.mockResolvedValue(
      '019b9999-9999-7999-8999-999999999999',
    );
    const api = { syncPushInstallation: jest.fn().mockResolvedValue(undefined) };
    const onSuccess = jest.fn();
    const onError = jest.fn();

    expect(addPushTokenSyncListener(api, onSuccess, onError)).toBe(subscription);
    await (
      tokenListener as unknown as (
        token: { type: 'android'; data: string },
      ) => Promise<void>
    )({ type: 'android', data: 'new-fcm-token' });

    expect(mockGetExpoPushTokenAsync).toHaveBeenCalledWith({
      projectId: 'd04128ba-7ddd-4df4-a952-d52a8a9bea68',
      devicePushToken: { type: 'android', data: 'new-fcm-token' },
    });
    expect(api.syncPushInstallation).toHaveBeenCalledWith(
      '019b9999-9999-7999-8999-999999999999',
      'ExponentPushToken[rotated]',
      'granted',
      '1.0.0',
    );
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  test('concurrent first registration attempts persist and use one installation ID', async () => {
    let storedId: string | null = null;
    mockGetPermissionsAsync.mockResolvedValue({
      status: ExpoNotifications.PermissionStatus.UNDETERMINED,
    } as never);
    mockGetItemAsync.mockImplementation(async () => storedId);
    mockSetItemAsync.mockImplementation(async (_key, value) => {
      await Promise.resolve();
      storedId = value;
    });
    mockRandomUUID
      .mockReturnValueOnce('019b1111-1111-7111-8111-111111111111')
      .mockReturnValueOnce('019b2222-2222-7222-8222-222222222222');
    const api = { syncPushInstallation: jest.fn().mockResolvedValue(undefined) };

    await Promise.all([
      syncCurrentNotificationState(api),
      syncCurrentNotificationState(api),
    ]);

    expect(
      api.syncPushInstallation.mock.calls.map(([id]) => id),
    ).toEqual([
      '019b1111-1111-7111-8111-111111111111',
      '019b1111-1111-7111-8111-111111111111',
    ]);
  });

  test('an older automatic sync cannot overwrite a later explicit grant', async () => {
    mockGetPermissionsAsync.mockResolvedValue({
      status: ExpoNotifications.PermissionStatus.UNDETERMINED,
    } as never);
    mockRequestPermissionsAsync.mockResolvedValue({
      status: ExpoNotifications.PermissionStatus.GRANTED,
    } as never);
    mockGetExpoPushTokenAsync.mockResolvedValue({
      type: 'expo',
      data: 'ExponentPushToken[granted]',
    });
    mockGetItemAsync.mockResolvedValue(
      '019b9999-9999-7999-8999-999999999999',
    );
    let finishFirst!: () => void;
    const firstCompletion = new Promise<void>((resolve) => {
      finishFirst = resolve;
    });
    let markFirstStarted!: () => void;
    const firstStarted = new Promise<void>((resolve) => {
      markFirstStarted = resolve;
    });
    let finalServerPermission: string | null = null;
    let requestIndex = 0;
    const api = {
      syncPushInstallation: jest.fn(
        async (
          _installationId: string,
          _token: string | null,
          permission: string,
        ) => {
          requestIndex += 1;
          if (requestIndex === 1) {
            markFirstStarted();
            await firstCompletion;
          }
          finalServerPermission = permission;
        },
      ),
    };

    const automatic = syncCurrentNotificationState(api);
    await firstStarted;
    const explicit = requestAndSync(api);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(api.syncPushInstallation).toHaveBeenCalledTimes(1);
    finishFirst();
    await Promise.all([automatic, explicit]);

    expect(finalServerPermission).toBe('granted');
  });
});
