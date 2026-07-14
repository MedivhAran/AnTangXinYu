import { ApiClient, SessionExpiredError, type FetchFunction } from './client';
import type { TokenPair } from './types';
import type { TokenStore } from '../auth/token-store';

const oldTokens: TokenPair = {
  accessToken: 'old-access',
  refreshToken: 'old-refresh',
  expiresIn: 900,
};
const newTokens: TokenPair = {
  accessToken: 'new-access',
  refreshToken: 'new-refresh',
  expiresIn: 900,
};

class MemoryTokenStore implements TokenStore {
  constructor(public tokens: TokenPair | null) {}
  clearCalls = 0;
  saveCalls: TokenPair[] = [];

  async load() {
    return this.tokens;
  }
  async save(tokens: TokenPair) {
    this.tokens = tokens;
    this.saveCalls.push(tokens);
  }
  async clear() {
    this.tokens = null;
    this.clearCalls += 1;
  }
}

function jsonResponse(status: number, value: unknown) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: new Headers({ 'content-type': 'application/json' }),
    body: null,
    json: async () => value,
  } as Awaited<ReturnType<FetchFunction>>;
}

describe('ApiClient authentication', () => {
  test('refreshes once after 401 and retries with the rotated access token', async () => {
    const store = new MemoryTokenStore(oldTokens);
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          access_token: newTokens.accessToken,
          refresh_token: newTokens.refreshToken,
          token_type: 'bearer',
          expires_in: newTokens.expiresIn,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          id: '019b1111-1111-7111-8111-111111111111',
          username: 'medivh',
        }),
      );
    const expired = jest.fn();
    const client = new ApiClient(
      'http://api.test',
      store,
      expired,
      fetchMock as FetchFunction,
    );

    await expect(client.me()).resolves.toEqual({
      id: '019b1111-1111-7111-8111-111111111111',
      username: 'medivh',
    });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[0][1].headers.get('Authorization')).toBe(
      'Bearer old-access',
    );
    expect(fetchMock.mock.calls[2][1].headers.get('Authorization')).toBe(
      'Bearer new-access',
    );
    expect(store.saveCalls).toEqual([newTokens]);
    expect(expired).not.toHaveBeenCalled();
  });

  test('clears credentials and reports expiry when refresh fails', async () => {
    const store = new MemoryTokenStore(oldTokens);
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'invalid refresh' }));
    const expired = jest.fn();
    const client = new ApiClient(
      'http://api.test',
      store,
      expired,
      fetchMock as FetchFunction,
    );

    await expect(client.me()).rejects.toBeInstanceOf(SessionExpiredError);
    expect(store.tokens).toBeNull();
    expect(store.clearCalls).toBe(1);
    expect(expired).toHaveBeenCalledTimes(1);
  });
});
