import {
  fetch as expoFetch,
  type FetchRequestInit,
} from 'expo/fetch';
import { File } from 'expo-file-system';

import type { TokenStore } from '../auth/token-store';
import { consumeChatStream } from './ndjson';
import {
  parseAuthResult,
  parseAttachmentUpload,
  parseHealthProfile,
  parseHealthProfileCardDecision,
  parseHealthProfileChangeResult,
  parseHealthProfileCards,
  parseMessageHistory,
  parseProactiveCareSettings,
  parseTokenPair,
  parseUser,
  parseWearableImportResponse,
  readErrorPayload,
  type ChatStreamEvent,
  type ChatAttachment,
  type HealthProfile,
  type HealthProfileCardAnswer,
  type HealthProfileCardDecision,
  type HealthProfileCards,
  type HealthProfileChange,
  type HealthProfileChangeResult,
  type MessageHistory,
  type ProactiveCareSettings,
  type TokenPair,
  type User,
  type WearableImportResponse,
  type WearableRecord,
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

export class SessionChangedError extends Error {
  constructor(message = '登录账号已发生变化，旧请求已停止。') {
    super(message);
    this.name = 'SessionChangedError';
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
  private refreshVersion = -1;
  private refreshToken: string | null = null;
  private sessionVersion = 0;
  private sessionController = new AbortController();
  private tokenMutationTail: Promise<void> = Promise.resolve();

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
    if (init.body !== undefined && !(init.body instanceof FormData)) {
      headers.set('Content-Type', 'application/json');
    }
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

  private async consumeChatResponse(
    response: FetchResponse,
    onEvent: (event: ChatStreamEvent) => void,
  ): Promise<void> {
    if (!response.ok) throw await this.apiError(response);

    const contentType = response.headers.get('content-type')?.split(';')[0].trim();
    if (contentType !== 'application/x-ndjson') {
      throw new Error(`聊天接口返回了错误的 Content-Type：${contentType ?? '缺失'}`);
    }
    if (response.body === null) throw new Error('聊天接口没有返回响应流');

    await consumeChatStream(response.body, onEvent);
  }

  private assertSessionVersion(version: number): void {
    if (version !== this.sessionVersion) throw new SessionChangedError();
  }

  getSessionSignal(): AbortSignal {
    return this.sessionController.signal;
  }

  private advanceSession(): number {
    this.sessionController.abort(new SessionChangedError());
    this.sessionController = new AbortController();
    return ++this.sessionVersion;
  }

  /** Serializes SecureStore writes so an older async request can never finish last. */
  private mutateTokens<T>(version: number, mutation: () => Promise<T>): Promise<T> {
    const result = this.tokenMutationTail.then(async () => {
      this.assertSessionVersion(version);
      return mutation();
    });
    this.tokenMutationTail = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  private async expireSession(expectedVersion: number): Promise<never> {
    this.assertSessionVersion(expectedVersion);
    const expiredVersion = this.advanceSession();
    await this.mutateTokens(expiredVersion, () => this.tokenStore.clear());
    this.assertSessionVersion(expiredVersion);
    this.onSessionExpired();
    throw new SessionExpiredError();
  }

  private async performRefresh(
    refreshToken: string,
    version: number,
  ): Promise<TokenPair> {
    const response = await this.fetchImpl(
      this.url('/api/v1/auth/refresh'),
      this.jsonInit({
        method: 'POST',
        body: JSON.stringify({ refresh_token: refreshToken }),
      }),
    );
    this.assertSessionVersion(version);
    if (!response.ok) throw await this.apiError(response);

    const tokens = parseTokenPair(await responseJson(response));
    await this.mutateTokens(version, async () => {
      const currentTokens = await this.tokenStore.load();
      this.assertSessionVersion(version);
      if (currentTokens?.refreshToken !== refreshToken) {
        throw new SessionChangedError();
      }
      await this.tokenStore.save(tokens);
    });
    return tokens;
  }

  private refresh(refreshToken: string, version: number): Promise<TokenPair> {
    if (
      this.refreshPromise !== null &&
      this.refreshVersion === version &&
      this.refreshToken === refreshToken
    ) {
      return this.refreshPromise;
    }

    const promise = this.performRefresh(refreshToken, version);
    this.refreshPromise = promise;
    this.refreshVersion = version;
    this.refreshToken = refreshToken;
    const clear = () => {
      if (this.refreshPromise !== promise) return;
      this.refreshPromise = null;
      this.refreshVersion = -1;
      this.refreshToken = null;
    };
    void promise.then(clear, clear);
    return promise;
  }

  private async authorizedFetch(
    path: string,
    init: FetchRequestInit = {},
  ): Promise<FetchResponse> {
    if (init.signal?.aborted) {
      throw init.signal.reason instanceof Error
        ? init.signal.reason
        : new SessionChangedError();
    }
    const version = this.sessionVersion;
    const originalTokens = await this.tokenStore.load();
    this.assertSessionVersion(version);
    if (originalTokens === null) return this.expireSession(version);

    let response = await this.fetchImpl(
      this.url(path),
      this.jsonInit(init, originalTokens.accessToken),
    );
    this.assertSessionVersion(version);
    if (response.status !== 401) return response;

    const latestTokens = await this.tokenStore.load();
    this.assertSessionVersion(version);
    if (latestTokens === null) return this.expireSession(version);

    if (latestTokens.accessToken !== originalTokens.accessToken) {
      response = await this.fetchImpl(
        this.url(path),
        this.jsonInit(init, latestTokens.accessToken),
      );
      this.assertSessionVersion(version);
      if (response.status !== 401) return response;
    }

    let refreshed: TokenPair;
    try {
      refreshed = await this.refresh(latestTokens.refreshToken, version);
    } catch (error) {
      if (error instanceof SessionChangedError) throw error;
      if (error instanceof ApiError && error.status === 401) {
        return this.expireSession(version);
      }
      throw error;
    }

    this.assertSessionVersion(version);
    response = await this.fetchImpl(
      this.url(path),
      this.jsonInit(init, refreshed.accessToken),
    );
    this.assertSessionVersion(version);
    if (response.status === 401) return this.expireSession(version);
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
    const version = this.advanceSession();
    await this.mutateTokens(version, () => this.tokenStore.clear());
    const response = await this.fetchImpl(
      this.url(path),
      this.jsonInit({
        method: 'POST',
        body: JSON.stringify({ username, password }),
      }),
    );
    this.assertSessionVersion(version);
    if (!response.ok) throw await this.apiError(response);

    const result = parseAuthResult(await responseJson(response));
    await this.mutateTokens(version, () => this.tokenStore.save(result.tokens));
    return result.user;
  }

  async me(signal?: AbortSignal): Promise<User> {
    const response = await this.authorizedFetch('/api/v1/auth/me', { signal });
    if (!response.ok) throw await this.apiError(response);
    return parseUser(await responseJson(response));
  }

  async logout(): Promise<void> {
    const version = this.sessionVersion;
    const tokens = await this.tokenStore.load();
    this.assertSessionVersion(version);
    if (tokens === null) return this.expireSession(version);
    const logoutVersion = this.advanceSession();

    const response = await this.fetchImpl(
      this.url('/api/v1/auth/logout'),
      this.jsonInit({
        method: 'POST',
        body: JSON.stringify({ refresh_token: tokens.refreshToken }),
      }),
    );
    this.assertSessionVersion(logoutVersion);
    if (response.status === 401) return this.expireSession(logoutVersion);
    if (!response.ok) throw await this.apiError(response);
    await this.mutateTokens(logoutVersion, () => this.tokenStore.clear());
  }

  async getMessages(before: string | null = null): Promise<MessageHistory> {
    const query = before === null ? '?limit=50' : `?before=${encodeURIComponent(before)}&limit=50`;
    const response = await this.authorizedFetch(`/api/v1/chat/messages${query}`);
    if (!response.ok) throw await this.apiError(response);
    return parseMessageHistory(await responseJson(response));
  }

  async getMessageWindow(messageId: string): Promise<MessageHistory> {
    const response = await this.authorizedFetch(
      `/api/v1/chat/messages/${encodeURIComponent(messageId)}/window`,
    );
    if (!response.ok) throw await this.apiError(response);
    return parseMessageHistory(await responseJson(response));
  }

  async getChatAttachmentImageSource(attachmentId: string): Promise<{
    uri: string;
    headers: { Authorization: string };
  }> {
    const version = this.sessionVersion;
    const tokens = await this.tokenStore.load();
    this.assertSessionVersion(version);
    if (tokens === null) return this.expireSession(version);
    return {
      uri: this.url(
        `/api/v1/chat/attachments/${encodeURIComponent(attachmentId)}/content`,
      ),
      headers: { Authorization: `Bearer ${tokens.accessToken}` },
    };
  }

  async syncPushInstallation(
    installationId: string,
    expoPushToken: string | null,
    permission: 'undetermined' | 'denied' | 'granted',
    appVersion: string,
  ): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/proactive-care/installations/${encodeURIComponent(installationId)}`,
      {
        method: 'PUT',
        body: JSON.stringify({
          expo_push_token: expoPushToken,
          permission,
          platform: 'android',
          app_version: appVersion,
        }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  async disablePushInstallation(installationId: string): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/proactive-care/installations/${encodeURIComponent(installationId)}`,
      { method: 'DELETE' },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  async markPushDeliveryOpened(deliveryId: string): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/proactive-care/deliveries/${encodeURIComponent(deliveryId)}/opened`,
      { method: 'POST' },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  async getProactiveCareSettings(): Promise<ProactiveCareSettings> {
    const response = await this.authorizedFetch(
      '/api/v1/proactive-care/settings',
    );
    if (!response.ok) throw await this.apiError(response);
    return parseProactiveCareSettings(await responseJson(response));
  }

  async putProactiveCareSettings(
    settings: ProactiveCareSettings,
  ): Promise<ProactiveCareSettings> {
    const response = await this.authorizedFetch(
      '/api/v1/proactive-care/settings',
      {
        method: 'PUT',
        body: JSON.stringify({
          routine_cadence: settings.routineCadence,
          plan_follow_up_enabled: settings.planFollowUpEnabled,
          health_events_enabled: settings.healthEventsEnabled,
          timezone: settings.timezone,
          quiet_hours_start: settings.quietHoursStart,
          quiet_hours_end: settings.quietHoursEnd,
          health_notification_preview_enabled:
            settings.healthNotificationPreviewEnabled,
        }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseProactiveCareSettings(await responseJson(response));
  }

  async getHealthProfileCards(): Promise<HealthProfileCards> {
    const response = await this.authorizedFetch('/api/v1/health-profile/cards?limit=50');
    if (!response.ok) throw await this.apiError(response);
    return parseHealthProfileCards(await responseJson(response));
  }

  async getHealthProfile(signal?: AbortSignal): Promise<HealthProfile> {
    const response = await this.authorizedFetch('/api/v1/health-profile', {
      signal,
    });
    if (!response.ok) throw await this.apiError(response);
    return parseHealthProfile(await responseJson(response));
  }

  async answerHealthProfileCard(
    cardId: string,
    clientActionId: string,
    answer: HealthProfileCardAnswer,
  ): Promise<HealthProfileCardDecision> {
    const customAnswer = answer.customAnswer;
    const body =
      customAnswer === undefined
        ? {
            client_action_id: clientActionId,
            option_id: answer.optionId,
          }
        : customAnswer.targetType === 'personal_profile'
          ? {
              client_action_id: clientActionId,
              custom_answer: {
                value: customAnswer.value,
                ...(customAnswer.unit === undefined
                  ? {}
                  : { unit: customAnswer.unit }),
              },
            }
          : {
              client_action_id: clientActionId,
              custom_answer: {
                statement: customAnswer.statement,
                assertion: customAnswer.assertion,
                temporal_status: customAnswer.temporalStatus,
              },
            };
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/cards/${encodeURIComponent(cardId)}/answer`,
      {
        method: 'POST',
        body: JSON.stringify(body),
      },
    );
    if (response.status === 409) {
      const payload = await responseJson(response);
      try {
        const result = parseHealthProfileCardDecision(payload);
        if (result.status === 'conflicted') return result;
      } catch {
        // A normal API error is handled below from the already-read payload.
      }
      const details = readErrorPayload(payload);
      throw new ApiError(
        details.message ?? '服务器返回 HTTP 409',
        response.status,
        details.code,
      );
    }
    if (!response.ok) throw await this.apiError(response);
    return parseHealthProfileCardDecision(await responseJson(response));
  }

  async changeHealthProfile(
    change: HealthProfileChange,
  ): Promise<HealthProfileChangeResult> {
    const body: Record<string, unknown> = {
      client_action_id: change.clientActionId,
      expected_revision: change.expectedRevision,
      target_type: change.targetType,
      operation: change.operation,
    };

    if ('fieldName' in change) body.field_name = change.fieldName;
    if ('targetId' in change) body.target_id = change.targetId;
    if ('value' in change) body.value = change.value;
    if ('unit' in change && change.unit !== undefined) body.unit = change.unit;
    if ('factType' in change) body.fact_type = change.factType;
    if ('statement' in change) body.statement = change.statement;
    if ('assertion' in change) body.assertion = change.assertion;
    if ('temporalStatus' in change) {
      body.temporal_status = change.temporalStatus;
    }

    const response = await this.authorizedFetch('/api/v1/health-profile/changes', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    if (!response.ok) throw await this.apiError(response);
    return parseHealthProfileChangeResult(await responseJson(response));
  }

  async importWearableRecords(
    clientSyncId: string,
    recordType: WearableRecord['record_type'],
    healthContextComplete: boolean,
    records: WearableRecord[],
    deletedRecordIds: string[],
    signal?: AbortSignal,
  ): Promise<WearableImportResponse> {
    const response = await this.authorizedFetch(
      '/api/v1/health-profile/wearable-imports',
      {
        method: 'POST',
        body: JSON.stringify({
          client_sync_id: clientSyncId,
          record_type: recordType,
          health_context_complete: healthContextComplete,
          records,
          deleted_record_ids: deletedRecordIds,
        }),
        signal,
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseWearableImportResponse(await responseJson(response));
  }

  async streamMessage(
    clientMessageId: string,
    content: string,
    attachmentId: string | null,
    onEvent: (event: ChatStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await this.authorizedFetch('/api/v1/chat/messages', {
      method: 'POST',
      body: JSON.stringify({
        client_message_id: clientMessageId,
        content,
        attachment_id: attachmentId,
      }),
      signal,
      headers: { Accept: 'application/x-ndjson' },
    });

    await this.consumeChatResponse(response, onEvent);
  }

  async uploadChatAttachment(attachment: {
    kind: 'photo' | 'report';
    mimeType: string;
    name: string;
    uri: string;
  }): Promise<ChatAttachment> {
    const form = new FormData();
    const file = new File(attachment.uri);
    form.append('kind', attachment.kind);
    form.append(
      'file',
      {
        bytes: () => file.bytes(),
        name: attachment.name,
        type: attachment.mimeType,
      } as unknown as Blob,
    );
    const response = await this.authorizedFetch('/api/v1/chat/attachments', {
      method: 'POST',
      body: form,
    });
    if (!response.ok) throw await this.apiError(response);
    return parseAttachmentUpload(await responseJson(response));
  }

  async retryMessage(
    failedAssistantMessageId: string,
    onEvent: (event: ChatStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/chat/messages/${encodeURIComponent(failedAssistantMessageId)}/retry`,
      {
        method: 'POST',
        signal,
        headers: { Accept: 'application/x-ndjson' },
      },
    );

    await this.consumeChatResponse(response, onEvent);
  }
}
