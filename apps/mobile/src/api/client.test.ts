import {
  ApiClient,
  SessionChangedError,
  SessionExpiredError,
  type FetchFunction,
} from './client';
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
const accountBTokens: TokenPair = {
  accessToken: 'account-b-access',
  refreshToken: 'account-b-refresh',
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

function ndjsonResponse(events: unknown[]) {
  const body = events.map((event) => JSON.stringify(event)).join('\n') + '\n';
  return {
    status: 200,
    ok: true,
    headers: new Headers({ 'content-type': 'application/x-ndjson' }),
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body));
        controller.close();
      },
    }),
    json: async () => {
      throw new Error('not json');
    },
  } as unknown as Awaited<ReturnType<FetchFunction>>;
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

  test('keeps credentials when refresh fails because the server is unavailable', async () => {
    const store = new MemoryTokenStore(oldTokens);
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      .mockResolvedValueOnce(jsonResponse(503, { detail: 'unavailable' }));
    const expired = jest.fn();
    const client = new ApiClient(
      'http://api.test',
      store,
      expired,
      fetchMock as FetchFunction,
    );

    await expect(client.me()).rejects.toMatchObject({ status: 503 });
    expect(store.tokens).toEqual(oldTokens);
    expect(store.clearCalls).toBe(0);
    expect(expired).not.toHaveBeenCalled();
  });

  test('a late refresh from account A cannot overwrite account B login', async () => {
    const store = new MemoryTokenStore(oldTokens);
    let markRefreshStarted!: () => void;
    const refreshStarted = new Promise<void>((resolve) => {
      markRefreshStarted = resolve;
    });
    let resolveRefresh!: (response: ReturnType<typeof jsonResponse>) => void;
    const pendingRefresh = new Promise<ReturnType<typeof jsonResponse>>((resolve) => {
      resolveRefresh = resolve;
    });
    const fetchMock = jest.fn((input: string | URL) => {
      const url = String(input);
      if (url.endsWith('/api/v1/auth/me')) {
        return Promise.resolve(jsonResponse(401, { detail: 'expired' }));
      }
      if (url.endsWith('/api/v1/auth/refresh')) {
        markRefreshStarted();
        return pendingRefresh;
      }
      if (url.endsWith('/api/v1/auth/login')) {
        return Promise.resolve(
          jsonResponse(200, {
            access_token: accountBTokens.accessToken,
            refresh_token: accountBTokens.refreshToken,
            token_type: 'bearer',
            expires_in: accountBTokens.expiresIn,
            user: {
              id: '019b2222-2222-7222-8222-222222222222',
              username: 'account-b',
            },
          }),
        );
      }
      throw new Error(`unexpected request: ${url}`);
    });
    const expired = jest.fn();
    const client = new ApiClient(
      'http://api.test',
      store,
      expired,
      fetchMock as FetchFunction,
    );

    const accountARequest = client.me();
    await refreshStarted;
    await expect(client.login('account-b', 'password-b')).resolves.toMatchObject({
      username: 'account-b',
    });

    resolveRefresh(
      jsonResponse(200, {
        access_token: newTokens.accessToken,
        refresh_token: newTokens.refreshToken,
        token_type: 'bearer',
        expires_in: newTokens.expiresIn,
      }),
    );

    await expect(accountARequest).rejects.toBeInstanceOf(SessionChangedError);
    expect(store.tokens).toEqual(accountBTokens);
    expect(store.saveCalls).toEqual([accountBTokens]);
    expect(expired).not.toHaveBeenCalled();
  });

  test('logout wins locally when an automatic refresh is already in flight', async () => {
    const store = new MemoryTokenStore(oldTokens);
    let markRefreshStarted!: () => void;
    const refreshStarted = new Promise<void>((resolve) => {
      markRefreshStarted = resolve;
    });
    let resolveRefresh!: (response: ReturnType<typeof jsonResponse>) => void;
    const pendingRefresh = new Promise<ReturnType<typeof jsonResponse>>((resolve) => {
      resolveRefresh = resolve;
    });
    let logoutRequestBody: unknown;
    const fetchMock = jest.fn((input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/api/v1/auth/me')) {
        return Promise.resolve(jsonResponse(401, { detail: 'expired' }));
      }
      if (url.endsWith('/api/v1/auth/refresh')) {
        markRefreshStarted();
        return pendingRefresh;
      }
      if (url.endsWith('/api/v1/auth/logout')) {
        logoutRequestBody = JSON.parse(String(init?.body));
        return Promise.resolve(jsonResponse(204, null));
      }
      throw new Error(`unexpected request: ${url}`);
    });
    const client = new ApiClient(
      'http://api.test',
      store,
      jest.fn(),
      fetchMock as FetchFunction,
    );

    const request = client.me();
    await refreshStarted;
    await expect(client.logout()).resolves.toBeUndefined();
    resolveRefresh(
      jsonResponse(200, {
        access_token: newTokens.accessToken,
        refresh_token: newTokens.refreshToken,
        token_type: 'bearer',
        expires_in: newTokens.expiresIn,
      }),
    );

    await expect(request).rejects.toBeInstanceOf(SessionChangedError);
    expect(store.tokens).toBeNull();
    expect(store.saveCalls).toEqual([]);
    expect(logoutRequestBody).toEqual({
      refresh_token: oldTokens.refreshToken,
    });
  });

  test('work bound to account A cannot start another upload after account B logs in', async () => {
    const store = new MemoryTokenStore(oldTokens);
    const fetchMock = jest.fn((input: string | URL) => {
      const url = String(input);
      if (url.endsWith('/api/v1/auth/login')) {
        return Promise.resolve(
          jsonResponse(200, {
            access_token: accountBTokens.accessToken,
            refresh_token: accountBTokens.refreshToken,
            token_type: 'bearer',
            expires_in: accountBTokens.expiresIn,
            user: {
              id: '019b2222-2222-7222-8222-222222222222',
              username: 'account-b',
            },
          }),
        );
      }
      throw new Error(`old session unexpectedly requested ${url}`);
    });
    const client = new ApiClient(
      'http://api.test',
      store,
      jest.fn(),
      fetchMock as FetchFunction,
    );
    const accountASignal = client.getSessionSignal();

    await client.login('account-b', 'password-b');

    await expect(
      client.importWearableRecords(
        '019b6666-6666-7666-8666-666666666666',
        'steps',
        false,
        [],
        [],
        accountASignal,
      ),
    ).rejects.toBeInstanceOf(SessionChangedError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('ApiClient chat', () => {
  test('retries a failed assistant message through the normal chat stream', async () => {
    const failedAssistantMessageId = '019b2222-2222-7222-8222-222222222222';
    const userMessageId = '019b1111-1111-7111-8111-111111111111';
    const newAssistantMessageId = '019b3333-3333-7333-8333-333333333333';
    const runId = '019b4444-4444-7444-8444-444444444444';
    const fetchMock = jest.fn().mockResolvedValueOnce(
      ndjsonResponse([
        {
          type: 'message_started',
          user_message_id: userMessageId,
          assistant_message_id: newAssistantMessageId,
          run_id: runId,
        },
        {
          type: 'message_completed',
          input_tokens: 12,
          output_tokens: 4,
          sources: [],
        },
      ]),
    );
    const client = new ApiClient(
      'http://api.test',
      new MemoryTokenStore(oldTokens),
      jest.fn(),
      fetchMock as FetchFunction,
    );
    const events: unknown[] = [];

    await client.retryMessage(failedAssistantMessageId, (event) => {
      events.push(event);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      `http://api.test/api/v1/chat/messages/${failedAssistantMessageId}/retry`,
    );
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' });
    expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
    expect(fetchMock.mock.calls[0][1].headers.get('Accept')).toBe(
      'application/x-ndjson',
    );
    expect(events).toEqual([
      {
        type: 'message_started',
        userMessageId,
        assistantMessageId: newAssistantMessageId,
        runId,
      },
      {
        type: 'message_completed',
        inputTokens: 12,
        outputTokens: 4,
        sources: [],
      },
    ]);
  });

  test('syncs a push installation and opens the requested message window', async () => {
    const installationId = '019b5555-5555-7555-8555-555555555555';
    const messageId = '019b6666-6666-7666-8666-666666666666';
    const deliveryId = '019b7777-7777-7777-8777-777777777777';
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse(204, null))
      .mockResolvedValueOnce(jsonResponse(204, null))
      .mockResolvedValueOnce(jsonResponse(204, null))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          messages: [
            {
              id: messageId,
              client_message_id: null,
              role: 'assistant',
              status: 'completed',
              content: '今天感觉怎么样？',
              sources: [],
              created_at: '2026-07-18T10:00:00Z',
              completed_at: '2026-07-18T10:00:01Z',
            },
          ],
          next_before: null,
        }),
      );
    const client = new ApiClient(
      'http://api.test',
      new MemoryTokenStore(oldTokens),
      jest.fn(),
      fetchMock as FetchFunction,
    );

    await client.syncPushInstallation(
      installationId,
      'ExponentPushToken[test]',
      'granted',
      '1.0.0',
    );
    await client.disablePushInstallation(installationId);
    await client.markPushDeliveryOpened(deliveryId);
    await expect(client.getMessageWindow(messageId)).resolves.toMatchObject({
      messages: [{ id: messageId, content: '今天感觉怎么样？' }],
    });

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      `http://api.test/api/v1/proactive-care/installations/${installationId}`,
      `http://api.test/api/v1/proactive-care/installations/${installationId}`,
      `http://api.test/api/v1/proactive-care/deliveries/${deliveryId}/opened`,
      `http://api.test/api/v1/chat/messages/${messageId}/window`,
    ]);
    expect(fetchMock.mock.calls.map((call) => call[1].method)).toEqual([
      'PUT',
      'DELETE',
      'POST',
      undefined,
    ]);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      expo_push_token: 'ExponentPushToken[test]',
      permission: 'granted',
      platform: 'android',
      app_version: '1.0.0',
    });
  });
});

describe('ApiClient health profile', () => {
  test('loads and parses the authenticated health overview', async () => {
    const fetchMock = jest.fn().mockResolvedValueOnce(
      jsonResponse(200, {
        personal_profile: {
          sex: null,
          age_years: null,
          age_as_of_date: null,
          height_cm: null,
          weight_kg: null,
          resident_area: null,
          schedule_type: null,
          occupation: null,
          revision: 0,
          updated_at: '2026-07-26T12:00:00+00:00',
        },
        health_facts: [],
        wearable_latest: [
          {
            record_type: 'resting_heart_rate',
            observed_at: '2026-07-26T07:00:00+08:00',
            data: { beats_per_minute: 63 },
            source_package: 'com.huami.watch.hmwatchmanager',
          },
        ],
      }),
    );
    const client = new ApiClient(
      'http://api.test',
      new MemoryTokenStore(oldTokens),
      jest.fn(),
      fetchMock as FetchFunction,
    );
    const controller = new AbortController();

    await expect(client.getHealthProfile(controller.signal)).resolves.toMatchObject({
      personalProfile: { revision: 0 },
      healthFacts: [],
      wearableLatest: [
        {
          recordType: 'resting_heart_rate',
          data: { beatsPerMinute: 63 },
        },
      ],
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://api.test/api/v1/health-profile',
    );
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get('Authorization')).toBe(
      'Bearer old-access',
    );
  });

  test('loads cards, sends answers and uploads normalized wearable records', async () => {
    const store = new MemoryTokenStore(oldTokens);
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(200, {
          cards: [
            {
              id: '019b3333-3333-7333-8333-333333333333',
              kind: 'confirmation',
              target_type: 'personal_profile',
              field_name: 'weight_kg',
              operation: 'set',
              question: '更新体重吗？',
              proposed_value: { value: 62 },
              options: [
                { id: 'kg', label: '62 公斤' },
                { id: 'reject', label: '暂不写入' },
              ],
              allow_custom_input: true,
              custom_input_placeholder: '输入体重和单位',
              created_at: '2026-07-14T10:00:00Z',
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          card_id: '019b3333-3333-7333-8333-333333333333',
          status: 'applied',
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          import_id: '019b5555-5555-7555-8555-555555555555',
          status: 'completed',
          records_created: 1,
          records_updated: 0,
          records_unchanged: 0,
          records_deleted: 0,
        }),
      );
    const client = new ApiClient(
      'http://api.test',
      store,
      jest.fn(),
      fetchMock as FetchFunction,
    );

    await expect(client.getHealthProfileCards()).resolves.toMatchObject({
      cards: [{ fieldName: 'weight_kg', proposedValue: { value: 62 } }],
    });
    await expect(
      client.answerHealthProfileCard(
        '019b3333-3333-7333-8333-333333333333',
        '019b4444-4444-7444-8444-444444444444',
        { optionId: 'kg' },
      ),
    ).resolves.toEqual({
      cardId: '019b3333-3333-7333-8333-333333333333',
      status: 'applied',
    });

    const record = {
      external_record_id: 'zepp-1',
      record_type: 'steps' as const,
      start_time: '2026-07-14T10:00:00Z',
      end_time: '2026-07-14T10:01:00Z',
      start_zone_offset_seconds: 28800,
      end_zone_offset_seconds: 28800,
      data: { count: 100 },
      source_package: 'com.huami.watch.hmwatchmanager',
      recording_method: 2,
      device: { manufacturer: 'Amazfit', model: 'Active 2', device_type: 6 },
      source_last_modified_at: '2026-07-14T10:02:00Z',
    };
    await client.importWearableRecords(
      '019b6666-6666-7666-8666-666666666666',
      'steps',
      false,
      [record],
      [],
    );

    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://api.test/api/v1/health-profile/cards?limit=50',
    );
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      client_action_id: '019b4444-4444-7444-8444-444444444444',
      option_id: 'kg',
    });
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({
      client_sync_id: '019b6666-6666-7666-8666-666666666666',
      record_type: 'steps',
      health_context_complete: false,
      records: [record],
      deleted_record_ids: [],
    });
  });

  test('sends typed card answers and one-target manual profile changes', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(200, {
          card_id: '019b3333-3333-7333-8333-333333333333',
          status: 'applied',
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          change_id: '019b7777-7777-7777-8777-777777777777',
          status: 'applied',
          target_type: 'health_fact',
          field_name: 'allergy',
          operation: 'add',
          target_id: '019b8888-8888-7888-8888-888888888888',
          result_revision: 1,
        }),
      );
    const client = new ApiClient(
      'http://api.test',
      new MemoryTokenStore(oldTokens),
      jest.fn(),
      fetchMock as FetchFunction,
    );

    await client.answerHealthProfileCard(
      '019b3333-3333-7333-8333-333333333333',
      '019b4444-4444-7444-8444-444444444444',
      {
        customAnswer: {
          targetType: 'personal_profile',
          value: '100',
          unit: 'jin',
        },
      },
    );
    await client.changeHealthProfile({
      clientActionId: '019b6666-6666-7666-8666-666666666666',
      expectedRevision: null,
      targetType: 'health_fact',
      operation: 'add',
      factType: 'allergy',
      statement: '对青霉素过敏',
      assertion: 'present',
      temporalStatus: 'current',
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://api.test/api/v1/health-profile/cards/019b3333-3333-7333-8333-333333333333/answer',
    );
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      client_action_id: '019b4444-4444-7444-8444-444444444444',
      custom_answer: { value: '100', unit: 'jin' },
    });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      client_action_id: '019b6666-6666-7666-8666-666666666666',
      expected_revision: null,
      target_type: 'health_fact',
      operation: 'add',
      fact_type: 'allergy',
      statement: '对青霉素过敏',
      assertion: 'present',
      temporal_status: 'current',
    });
  });

  test('returns only a structurally valid conflicted decision from HTTP 409', async () => {
    const store = new MemoryTokenStore(oldTokens);
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(409, {
          card_id: '019b3333-3333-7333-8333-333333333333',
          status: 'conflicted',
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(409, {
          detail: {
            code: 'profile_card_changed',
            message: 'the card changed',
          },
        }),
      );
    const client = new ApiClient(
      'http://api.test',
      store,
      jest.fn(),
      fetchMock as FetchFunction,
    );

    await expect(
      client.answerHealthProfileCard(
        '019b3333-3333-7333-8333-333333333333',
        '019b4444-4444-7444-8444-444444444444',
        { optionId: 'accept' },
      ),
    ).resolves.toEqual({
      cardId: '019b3333-3333-7333-8333-333333333333',
      status: 'conflicted',
    });
    await expect(
      client.answerHealthProfileCard(
        '019b3333-3333-7333-8333-333333333333',
        '019b5555-5555-7555-8555-555555555555',
        { optionId: 'accept' },
      ),
    ).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      code: 'profile_card_changed',
    });
  });
});

describe('ApiClient proactive care settings', () => {
  const serverSettings = {
    routine_cadence: 'disabled',
    plan_follow_up_enabled: true,
    health_events_enabled: false,
    timezone: 'Asia/Shanghai',
    quiet_hours_start: '22:00',
    quiet_hours_end: '08:00',
    health_notification_preview_enabled: false,
  };

  test('loads and replaces the complete settings document', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, serverSettings))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          ...serverSettings,
          routine_cadence: 'every_3_days',
          health_events_enabled: true,
          timezone: 'America/New_York',
          quiet_hours_start: '21:30',
          quiet_hours_end: '07:15',
          health_notification_preview_enabled: true,
        }),
      );
    const client = new ApiClient(
      'http://api.test',
      new MemoryTokenStore(oldTokens),
      jest.fn(),
      fetchMock as FetchFunction,
    );

    await expect(client.getProactiveCareSettings()).resolves.toMatchObject({
      routineCadence: 'disabled',
      timezone: 'Asia/Shanghai',
    });
    await expect(
      client.putProactiveCareSettings({
        routineCadence: 'every_3_days',
        planFollowUpEnabled: true,
        healthEventsEnabled: true,
        timezone: 'America/New_York',
        quietHoursStart: '21:30',
        quietHoursEnd: '07:15',
        healthNotificationPreviewEnabled: true,
      }),
    ).resolves.toMatchObject({
      routineCadence: 'every_3_days',
      healthEventsEnabled: true,
    });

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      'http://api.test/api/v1/proactive-care/settings',
      'http://api.test/api/v1/proactive-care/settings',
    ]);
    expect(fetchMock.mock.calls[1][1].method).toBe('PUT');
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      routine_cadence: 'every_3_days',
      plan_follow_up_enabled: true,
      health_events_enabled: true,
      timezone: 'America/New_York',
      quiet_hours_start: '21:30',
      quiet_hours_end: '07:15',
      health_notification_preview_enabled: true,
    });
  });
});
