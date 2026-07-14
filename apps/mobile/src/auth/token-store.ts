import * as SecureStore from 'expo-secure-store';

import type { TokenPair } from '../api/types';

const TOKEN_KEY = 'antang.auth.tokens';

export interface TokenStore {
  load(): Promise<TokenPair | null>;
  save(tokens: TokenPair): Promise<void>;
  clear(): Promise<void>;
}

function parseStoredTokens(raw: string): TokenPair {
  const value: unknown = JSON.parse(raw);
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('本地登录凭证格式错误');
  }

  const data = value as Record<string, unknown>;
  if (
    typeof data.accessToken !== 'string' ||
    data.accessToken.length === 0 ||
    typeof data.refreshToken !== 'string' ||
    data.refreshToken.length === 0 ||
    !Number.isInteger(data.expiresIn) ||
    (data.expiresIn as number) < 0
  ) {
    throw new Error('本地登录凭证字段错误');
  }

  return data as TokenPair;
}

export const secureTokenStore: TokenStore = {
  async load() {
    const raw = await SecureStore.getItemAsync(TOKEN_KEY);
    return raw === null ? null : parseStoredTokens(raw);
  },

  async save(tokens) {
    await SecureStore.setItemAsync(TOKEN_KEY, JSON.stringify(tokens));
  },

  async clear() {
    await SecureStore.deleteItemAsync(TOKEN_KEY);
  },
};
