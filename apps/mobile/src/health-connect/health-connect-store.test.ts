import * as SecureStore from 'expo-secure-store';

import {
  parseStoredBackgroundState,
  parseStoredChangeTokens,
  secureHealthConnectTokenStore,
} from './health-connect-store';

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
}));

describe('Health Connect token storage', () => {
  beforeEach(() => jest.clearAllMocks());

  test('parses tokens for the approved record types', () => {
    expect(
      parseStoredChangeTokens(JSON.stringify({ Steps: 'steps-1', SleepSession: 'sleep-4' })),
    ).toEqual({ Steps: 'steps-1', SleepSession: 'sleep-4' });
  });

  test('rejects unknown types and empty tokens', () => {
    expect(() => parseStoredChangeTokens('{"Unknown":"token"}')).toThrow(
      '未知类型',
    );
    expect(() => parseStoredChangeTokens('{"Steps":""}')).toThrow('Steps 无效');
  });

  test('persists foreground completion and an expired background cursor', async () => {
    const getItemAsync = jest.mocked(SecureStore.getItemAsync);
    const setItemAsync = jest.mocked(SecureStore.setItemAsync);
    getItemAsync
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(
        JSON.stringify({
          initialSyncComplete: true,
          backgroundEnabled: true,
          needsForeground: false,
          lastRunAt: null,
          lastError: null,
        }),
      );

    await secureHealthConnectTokenStore.markInitialSyncComplete('user-1');
    await secureHealthConnectTokenStore.recordBackgroundFailure(
      'user-1',
      '游标失效',
      true,
      '2026-07-18T10:00:00.000Z',
    );

    expect(setItemAsync).toHaveBeenNthCalledWith(
      1,
      'antang.health-connect.background.user-1',
      JSON.stringify({
        initialSyncComplete: true,
        backgroundEnabled: false,
        needsForeground: false,
        lastRunAt: null,
        lastError: null,
      }),
    );
    expect(setItemAsync).toHaveBeenNthCalledWith(
      2,
      'antang.health-connect.background.user-1',
      JSON.stringify({
        initialSyncComplete: true,
        backgroundEnabled: true,
        needsForeground: true,
        lastRunAt: '2026-07-18T10:00:00.000Z',
        lastError: '游标失效',
      }),
    );
  });

  test('rejects malformed background state instead of silently resetting it', () => {
    expect(() =>
      parseStoredBackgroundState(
        JSON.stringify({
          initialSyncComplete: 'yes',
          backgroundEnabled: false,
          needsForeground: false,
          lastRunAt: null,
          lastError: null,
        }),
      ),
    ).toThrow('后台同步状态');
  });

  test('persists an explicit background disable', async () => {
    jest.mocked(SecureStore.getItemAsync).mockResolvedValueOnce(
      JSON.stringify({
        initialSyncComplete: true,
        backgroundEnabled: true,
        needsForeground: true,
        lastRunAt: '2026-07-18T10:00:00.000Z',
        lastError: '游标失效',
      }),
    );

    await secureHealthConnectTokenStore.disableBackground('user-1');

    expect(SecureStore.setItemAsync).toHaveBeenCalledWith(
      'antang.health-connect.background.user-1',
      JSON.stringify({
        initialSyncComplete: true,
        backgroundEnabled: false,
        needsForeground: false,
        lastRunAt: '2026-07-18T10:00:00.000Z',
        lastError: null,
      }),
    );
  });
});
