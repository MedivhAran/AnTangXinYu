import {
  fetch as expoFetch,
  type FetchRequestInit,
} from 'expo/fetch';

import type { TokenStore } from '../auth/token-store';
import { consumeChatStream } from './ndjson';
import {
  parseAuthResult,
  parseMessageHistory,
  parseTokenPair,
  parseUser,
  readErrorPayload,
  type ChatStreamEvent,
  type MessageHistory,
  type TokenPair,
  type User,
} from './types';

export type FetchFunction = (
  input: string | URL,
  init?: FetchRequestInit,
) => ReturnType<typeof expoFetch>;

type FetchResponse = Awaited<ReturnType<typeof expoFetch>>;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export class SessionExpiredError extends Error {
  constructor(message = '登录已过期，请重新登录。') {
    super(message);
    this.name = 'SessionExpiredError';
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '发生了未知错误';
}

async function responseJson(response: FetchResponse): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new Error(`服务器返回 HTTP ${response.status}，响应内容不是有效 JSON`);
  }
}

export class ApiClient {
  private refreshPromise: Promise<TokenPair> | null = null;

  constructor(
    private readonly baseUrl: string,
    private readonly tokenStore: TokenStore,
    private readonly onSessionExpired: () => void,
    private readonly fetchImpl: FetchFunction = expoFetch,
  ) {}

  private url(path: string): string {
    return `${this.baseUrl}${path}`;
  }

  private jsonInit(init: FetchRequestInit = {}, accessToken?: string): FetchRequestInit {
    const headers = new Headers(init.headers);
    if (!headers.has('Accept')) headers.set('Accept', 'application/json');
    if (init.body !== undefined) headers.set('Content-Type', 'application/json');
    if (accessToken !== undefined) headers.set('Authorization', `Bearer ${accessToken}`);
    return { ...init, headers };
  }

  private async apiError(response: FetchResponse): Promise<ApiError> {
    const payload = await responseJson(response);
    const details = readErrorPayload(payload);
    return new ApiError(
      details.message ?? `服务器返回 HTTP ${response.status}`,
      response.status,
      details.code,
    );
  }

  private async expireSession(): Promise<never> {
    await this.tokenStore.clear();
    this.onSessionExpired();
    throw new SessionExpiredError();
  }

  private async performRefresh(refreshToken: string): Promise<TokenPair> {
    const response = await this.fetchImpl(
      this.url('/api/v1/auth/refresh'),
      this.jsonInit({
        method: 'POST',
        body: JSON.stringify({ refresh_token: refreshToken }),
      }),
    );
    if (!response.ok) throw await this.apiError(response);

    const tokens = parseTokenPair(await responseJson(response));
    await this.tokenStore.save(tokens);
    return tokens;
  }

  private refresh(refreshToken: string): Promise<TokenPair> {
    if (this.refreshPromise === null) {
      this.refreshPromise = this.performRefresh(refreshToken).finally(() => {
        this.refreshPromise = null;
      });
    }
    return this.refreshPromise;
  }

  private async authorizedFetch(
    path: string,
    init: FetchRequestInit = {},
  ): Promise<FetchResponse> {
    const originalTokens = await this.tokenStore.load();
    if (originalTokens === null) return this.expireSession();

    let response = await this.fetchImpl(
      this.url(path),
      this.jsonInit(init, originalTokens.accessToken),
    );
    if (response.status !== 401) return response;

    const latestTokens = await this.tokenStore.load();
    if (latestTokens === null) return this.expireSession();

    if (latestTokens.accessToken !== originalTokens.accessToken) {
      response = await this.fetchImpl(
        this.url(path),
        this.jsonInit(init, latestTokens.accessToken),
      );
      if (response.status !== 401) return response;
    }

    let refreshed: TokenPair;
    try {
      refreshed = await this.refresh(latestTokens.refreshToken);
    } catch {
      return this.expireSession();
    }

    response = await this.fetchImpl(
      this.url(path),
      this.jsonInit(init, refreshed.accessToken),
    );
    if (response.status === 401) return this.expireSession();
    return response;
  }

  async login(username: string, password: string): Promise<User> {
    return this.authenticate('/api/v1/auth/login', username, password);
  }

  async register(username: string, password: string): Promise<User> {
    return this.authenticate('/api/v1/auth/register', username, password);
  }

  private async authenticate(
    path: string,
    username: string,
    password: string,
  ): Promise<User> {
    const response = await this.fetchImpl(
      this.url(path),
      this.jsonInit({
        method: 'POST',
        body: JSON.stringify({ username, password }),
      }),
    );
    if (!response.ok) throw await this.apiError(response);

    const result = parseAuthResult(await responseJson(response));
    await this.tokenStore.save(result.tokens);
    return result.user;
  }

  async me(): Promise<User> {
    const response = await this.authorizedFetch('/api/v1/auth/me');
    if (!response.ok) throw await this.apiError(response);
    return parseUser(await responseJson(response));
  }

  async logout(): Promise<void> {
    const tokens = await this.tokenStore.load();
    if (tokens === null) return this.expireSession();

    const response = await this.fetchImpl(
      this.url('/api/v1/auth/logout'),
      this.jsonInit({
        method: 'POST',
        body: JSON.stringify({ refresh_token: tokens.refreshToken }),
      }),
    );
    if (response.status === 401) return this.expireSession();
    if (!response.ok) throw await this.apiError(response);
    await this.tokenStore.clear();
  }

  async getMessages(before: string | null = null): Promise<MessageHistory> {
    const query = before === null ? '?limit=50' : `?before=${encodeURIComponent(before)}&limit=50`;
    const response = await this.authorizedFetch(`/api/v1/chat/messages${query}`);
    if (!response.ok) throw await this.apiError(response);
    return parseMessageHistory(await responseJson(response));
  }

  async streamMessage(
    clientMessageId: string,
    content: string,
    onEvent: (event: ChatStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await this.authorizedFetch('/api/v1/chat/messages', {
      method: 'POST',
      body: JSON.stringify({
        client_message_id: clientMessageId,
        content,
      }),
      signal,
      headers: { Accept: 'application/x-ndjson' },
    });

    if (!response.ok) throw await this.apiError(response);

    const contentType = response.headers.get('content-type')?.split(';')[0].trim();
    if (contentType !== 'application/x-ndjson') {
      throw new Error(`聊天接口返回了错误的 Content-Type：${contentType ?? '缺失'}`);
    }
    if (response.body === null) throw new Error('聊天接口没有返回响应流');

    await consumeChatStream(response.body, onEvent);
  }
}
