import * as SecureStore from 'expo-secure-store';

import {
  healthConnectRecordTypes,
  type HealthConnectChangeTokens,
  type HealthConnectTokenStore,
  type SupportedHealthConnectRecordType,
} from './health-connect-types';

const ACTIVE_USER_KEY = 'antang.health-connect.active-user';
const TOKEN_KEY_PREFIX = 'antang.health-connect.change-tokens.';
const BACKGROUND_KEY_PREFIX = 'antang.health-connect.background.';
const knownRecordTypes = new Set<string>(healthConnectRecordTypes);

export type HealthConnectBackgroundState = {
  initialSyncComplete: boolean;
  backgroundEnabled: boolean;
  needsForeground: boolean;
  lastRunAt: string | null;
  lastError: string | null;
};

export interface HealthConnectStore extends HealthConnectTokenStore {
  loadBackgroundState(userId: string): Promise<HealthConnectBackgroundState>;
  markInitialSyncComplete(userId: string): Promise<void>;
  enableBackground(userId: string): Promise<void>;
  disableBackground(userId: string): Promise<void>;
  recordBackgroundSuccess(userId: string, now: string): Promise<void>;
  recordBackgroundFailure(
    userId: string,
    message: string,
    needsForeground: boolean,
    now: string,
  ): Promise<void>;
}

const initialBackgroundState: HealthConnectBackgroundState = {
  initialSyncComplete: false,
  backgroundEnabled: false,
  needsForeground: false,
  lastRunAt: null,
  lastError: null,
};

function tokenKey(userId: string): string {
  return `${TOKEN_KEY_PREFIX}${userId}`;
}

function backgroundKey(userId: string): string {
  return `${BACKGROUND_KEY_PREFIX}${userId}`;
}

export function parseStoredChangeTokens(raw: string): HealthConnectChangeTokens {
  const value: unknown = JSON.parse(raw);
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('本地 Health Connect 游标格式错误');
  }

  const tokens: HealthConnectChangeTokens = {};
  for (const [recordType, token] of Object.entries(value)) {
    if (!knownRecordTypes.has(recordType)) {
      throw new Error(`本地 Health Connect 游标包含未知类型：${recordType}`);
    }
    if (typeof token !== 'string' || token.length === 0) {
      throw new Error(`本地 Health Connect 游标 ${recordType} 无效`);
    }
    tokens[recordType as SupportedHealthConnectRecordType] = token;
  }
  return tokens;
}

export function parseStoredBackgroundState(
  raw: string,
): HealthConnectBackgroundState {
  const value: unknown = JSON.parse(raw);
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('本地 Health Connect 后台同步状态格式错误');
  }
  const state = value as Record<string, unknown>;
  const validDate =
    state.lastRunAt === null ||
    (typeof state.lastRunAt === 'string' &&
      !Number.isNaN(Date.parse(state.lastRunAt)));
  const validError =
    state.lastError === null ||
    (typeof state.lastError === 'string' && state.lastError.length > 0);
  if (
    typeof state.initialSyncComplete !== 'boolean' ||
    typeof state.backgroundEnabled !== 'boolean' ||
    typeof state.needsForeground !== 'boolean' ||
    !validDate ||
    !validError
  ) {
    throw new Error('本地 Health Connect 后台同步状态字段错误');
  }
  return state as HealthConnectBackgroundState;
}

export const secureHealthConnectTokenStore: HealthConnectStore = {
  getActiveUserId() {
    return SecureStore.getItemAsync(ACTIVE_USER_KEY);
  },

  async setActiveUserId(userId) {
    await SecureStore.setItemAsync(ACTIVE_USER_KEY, userId);
  },

  async loadTokens(userId) {
    const raw = await SecureStore.getItemAsync(tokenKey(userId));
    return raw === null ? {} : parseStoredChangeTokens(raw);
  },

  async saveToken(userId, recordType, token) {
    if (token.length === 0) throw new Error('Health Connect 返回了空游标');
    const tokens = await this.loadTokens(userId);
    tokens[recordType] = token;
    await SecureStore.setItemAsync(tokenKey(userId), JSON.stringify(tokens));
  },

  async loadBackgroundState(userId: string): Promise<HealthConnectBackgroundState> {
    const raw = await SecureStore.getItemAsync(backgroundKey(userId));
    return raw === null
      ? { ...initialBackgroundState }
      : parseStoredBackgroundState(raw);
  },

  async markInitialSyncComplete(userId: string): Promise<void> {
    const state = await this.loadBackgroundState(userId);
    await SecureStore.setItemAsync(
      backgroundKey(userId),
      JSON.stringify({
        ...state,
        initialSyncComplete: true,
        needsForeground: false,
        lastError: null,
      }),
    );
  },

  async enableBackground(userId: string): Promise<void> {
    const state = await this.loadBackgroundState(userId);
    await SecureStore.setItemAsync(
      backgroundKey(userId),
      JSON.stringify({ ...state, backgroundEnabled: true }),
    );
  },

  async disableBackground(userId: string): Promise<void> {
    const state = await this.loadBackgroundState(userId);
    await SecureStore.setItemAsync(
      backgroundKey(userId),
      JSON.stringify({
        ...state,
        backgroundEnabled: false,
        needsForeground: false,
        lastError: null,
      }),
    );
  },

  async recordBackgroundSuccess(userId: string, now: string): Promise<void> {
    const state = await this.loadBackgroundState(userId);
    await SecureStore.setItemAsync(
      backgroundKey(userId),
      JSON.stringify({
        ...state,
        needsForeground: false,
        lastRunAt: now,
        lastError: null,
      }),
    );
  },

  async recordBackgroundFailure(
    userId: string,
    message: string,
    needsForeground: boolean,
    now: string,
  ): Promise<void> {
    const state = await this.loadBackgroundState(userId);
    await SecureStore.setItemAsync(
      backgroundKey(userId),
      JSON.stringify({
        ...state,
        needsForeground,
        lastRunAt: now,
        lastError: message,
      }),
    );
  },
};
