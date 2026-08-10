import { appClient, setSessionExpiredListener } from './app-client';
import { ApiClient } from './client';

jest.mock('./client', () => ({
  ApiClient: jest.fn().mockImplementation(
    (_url: string, _store: unknown, onSessionExpired: () => void) => ({
      expireForTest: onSessionExpired,
    }),
  ),
}));
jest.mock('../config', () => ({ API_URL: 'http://api.test' }));
jest.mock('../auth/token-store', () => ({ secureTokenStore: {} }));

describe('shared app API client', () => {
  test('keeps one client and forwards expiry to the current UI listener', () => {
    const client = appClient as unknown as { expireForTest: () => void };
    const first = jest.fn();
    const second = jest.fn();

    const removeFirst = setSessionExpiredListener(first);
    client.expireForTest();
    setSessionExpiredListener(second);
    removeFirst();
    client.expireForTest();

    expect(ApiClient).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });
});
