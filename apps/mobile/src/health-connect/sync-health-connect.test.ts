import {
  healthConnectRecordTypes,
  type HealthConnectChangeTokens,
  type HealthConnectGateway,
  type HealthConnectTokenStore,
  type SupportedHealthConnectRecordType,
} from './health-connect-types';
import {
  runHealthConnectSync,
  syncHealthConnect,
  type WearableImporter,
} from './sync-health-connect';

let mockUuid = 0;
jest.mock('expo-crypto', () => ({
  randomUUID: () =>
    `00000000-0000-4000-8000-${String(++mockUuid).padStart(12, '0')}`,
}));

const metadata = (id: string, modified: string) => ({
  id,
  dataOrigin: 'com.huami.watch.hmwatchmanager',
  lastModifiedTime: modified,
  recordingMethod: 2,
  device: { manufacturer: 'Amazfit', model: 'Active 2', type: 6 },
});

function stepsRecord(id: string, count: number, modified: string) {
  return {
    startTime: '2026-07-14T10:00:00Z',
    endTime: '2026-07-14T10:01:00Z',
    startZoneOffset: { id: '+08:00', totalSeconds: 28800 },
    endZoneOffset: { id: '+08:00', totalSeconds: 28800 },
    count,
    metadata: metadata(id, modified),
  };
}

function allTokensExcept(
  missing?: SupportedHealthConnectRecordType,
): HealthConnectChangeTokens {
  return Object.fromEntries(
    healthConnectRecordTypes
      .filter((recordType) => recordType !== missing)
      .map((recordType) => [recordType, `${recordType}-token`]),
  );
}

describe('syncHealthConnect', () => {
  test('shares one in-flight sync between foreground and background callers', async () => {
    let finish!: () => void;
    const pending = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const loadTokens = jest.fn(() => pending.then(() => allTokensExcept()));
    const gateway = {
      getChanges: jest.fn(async (recordType: SupportedHealthConnectRecordType) => ({
        upsertionChanges: [],
        deletionChanges: [],
        nextChangesToken: `${recordType}-next`,
        changesTokenExpired: false,
        hasMore: false,
      })),
    } as unknown as HealthConnectGateway;
    const tokenStore = {
      loadTokens,
      saveToken: jest.fn(),
    } as unknown as HealthConnectTokenStore;
    const importer = { importWearableRecords: jest.fn(), listHealthConnectRecordIds: jest.fn() };

    const foreground = runHealthConnectSync(
      '019b1111-1111-7111-8111-111111111111',
      gateway,
      tokenStore,
      importer,
    );
    const background = runHealthConnectSync(
      '019b1111-1111-7111-8111-111111111111',
      gateway,
      tokenStore,
      importer,
    );

    expect(background).toBe(foreground);
    expect(loadTokens).toHaveBeenCalledTimes(1);
    finish();
    await foreground;
  });

  test('does not let a second account reuse another account sync', async () => {
    let finish!: () => void;
    const pending = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const tokenStore = {
      loadTokens: jest.fn(() => pending.then(() => allTokensExcept())),
      saveToken: jest.fn(),
    } as unknown as HealthConnectTokenStore;
    const gateway = {
      getChanges: jest.fn(async (recordType: SupportedHealthConnectRecordType) => ({
        upsertionChanges: [],
        deletionChanges: [],
        nextChangesToken: `${recordType}-next`,
        changesTokenExpired: false,
        hasMore: false,
      })),
    } as unknown as HealthConnectGateway;
    const importer = { importWearableRecords: jest.fn(), listHealthConnectRecordIds: jest.fn() };

    const first = runHealthConnectSync(
      '019b1111-1111-7111-8111-111111111111',
      gateway,
      tokenStore,
      importer,
    );
    expect(() =>
      runHealthConnectSync(
        '019b2222-2222-7222-8222-222222222222',
        gateway,
        tokenStore,
        importer,
      ),
    ).toThrow('另一个账号');
    finish();
    await first;
  });

  test('reads after the baseline token, applies newer changes and lets deletion win', async () => {
    const events: string[] = [];
    const tokens = allTokensExcept('Steps');
    let stepChangesCall = 0;
    const gateway: HealthConnectGateway = {
      getSdkStatus: jest.fn(),
      initialize: jest.fn(),
      requestReadPermissions: jest.fn(),
      requestHistoryReadPermission: jest.fn(),
      getGrantedPermissions: jest.fn(),
      async readRecords(recordType, startTime, endTime) {
        expect(recordType).toBe('Steps');
        expect(Date.parse(endTime) - Date.parse(startTime)).toBe(
          30 * 24 * 60 * 60 * 1000,
        );
        events.push('read');
        return {
          records: [
            stepsRecord('updated', 100, '2026-07-14T10:01:00Z'),
            stepsRecord('deleted', 50, '2026-07-14T10:01:00Z'),
          ],
        };
      },
      async getChanges(recordType, changeToken) {
        if (recordType !== 'Steps') {
          return {
            upsertionChanges: [],
            deletionChanges: [],
            nextChangesToken: `${recordType}-next`,
            changesTokenExpired: false,
            hasMore: false,
          };
        }
        stepChangesCall += 1;
        if (stepChangesCall === 1) {
          expect(changeToken).toBeUndefined();
          events.push('baseline');
          return {
            upsertionChanges: [],
            deletionChanges: [],
            nextChangesToken: 'steps-baseline',
            changesTokenExpired: false,
            hasMore: false,
          };
        }
        expect(changeToken).toBe('steps-baseline');
        events.push('catch-up');
        return {
          upsertionChanges: [
            {
              record: {
                recordType: 'Steps',
                ...stepsRecord('updated', 200, '2026-07-14T10:02:00Z'),
              },
            },
          ],
          deletionChanges: [{ recordId: 'deleted' }],
          nextChangesToken: 'steps-final',
          changesTokenExpired: false,
          hasMore: false,
        };
      },
    };
    const tokenStore: HealthConnectTokenStore = {
      getActiveUserId: jest.fn(),
      setActiveUserId: jest.fn(),
      loadTokens: jest.fn(async () => ({ ...tokens })),
      async saveToken(_userId, recordType, token) {
        events.push(`save:${recordType}:${token}`);
        tokens[recordType] = token;
      },
    };
    const imports: Parameters<WearableImporter['importWearableRecords']>[] = [];
    const importer: WearableImporter = {
      listHealthConnectRecordIds: jest.fn(),
      async importWearableRecords(...args) {
        imports.push(args);
        events.push(`import:${args[3][0]?.record_type ?? args[1]}`);
      },
    };

    await syncHealthConnect(
      '019b1111-1111-7111-8111-111111111111',
      gateway,
      tokenStore,
      importer,
      undefined,
      new Date('2026-07-14T12:00:00Z'),
    );

    expect(events.slice(0, 5)).toEqual([
      'baseline',
      'read',
      'catch-up',
      'import:steps',
      'save:Steps:steps-final',
    ]);
    expect(imports[0][1]).toBe('steps');
    expect(imports[0][2]).toBe(false);
    expect(imports[0][3]).toHaveLength(1);
    expect(imports[0][3][0]).toMatchObject({
      external_record_id: 'updated',
      data: { count: 200 },
    });
    expect(imports[0][4]).toEqual(['deleted']);
    const heartRateImport = imports.find((args) => args[1] === 'heart_rate');
    expect(imports.map((args) => args[1]).slice(0, 3)).toEqual([
      'steps',
      'exercise',
      'sleep',
    ]);
    expect(heartRateImport?.[2]).toBe(true);
  });

  test('does not retry or advance a token when the server import fails', async () => {
    const tokens = allTokensExcept();
    const getChanges = jest.fn(async () => ({
      upsertionChanges: [
        {
          record: {
            recordType: 'Steps',
            ...stepsRecord('one', 100, '2026-07-14T10:02:00Z'),
          },
        },
      ],
      deletionChanges: [],
      nextChangesToken: 'steps-next',
      changesTokenExpired: false,
      hasMore: false,
    }));
    const gateway = {
      getChanges,
    } as unknown as HealthConnectGateway;
    const saveToken = jest.fn();
    const tokenStore = {
      loadTokens: jest.fn(async () => tokens),
      saveToken,
    } as unknown as HealthConnectTokenStore;
    const importWearableRecords = jest.fn(async () => {
      throw new Error('server rejected import');
    });

    await expect(
      syncHealthConnect(
        '019b1111-1111-7111-8111-111111111111',
        gateway,
        tokenStore,
        { importWearableRecords, listHealthConnectRecordIds: jest.fn() },
      ),
    ).rejects.toThrow('server rejected import');
    expect(getChanges).toHaveBeenCalledTimes(1);
    expect(importWearableRecords).toHaveBeenCalledTimes(1);
    expect(saveToken).not.toHaveBeenCalled();
  });

  test('recovers an expired ExerciseSession cursor from full history before deleting missing records', async () => {
    const tokens = allTokensExcept();
    const events: string[] = [];
    let exerciseCalls = 0;
    const gateway = {
      async getChanges(recordType: SupportedHealthConnectRecordType, token?: string) {
        if (recordType === 'ExerciseSession') {
          exerciseCalls += 1;
          if (exerciseCalls === 1) {
            expect(token).toBe('ExerciseSession-token');
            return {
              upsertionChanges: [], deletionChanges: [], nextChangesToken: 'expired-token',
              changesTokenExpired: true, hasMore: false,
            };
          }
          if (exerciseCalls === 2) expect(token).toBeUndefined();
          if (exerciseCalls === 3) expect(token).toBe('exercise-baseline');
          return {
            upsertionChanges: [], deletionChanges: [],
            nextChangesToken: exerciseCalls === 2 ? 'exercise-baseline' : 'exercise-final',
            changesTokenExpired: false, hasMore: false,
          };
        }
        return {
          upsertionChanges: [], deletionChanges: [], nextChangesToken: `${recordType}-next`,
          changesTokenExpired: false, hasMore: false,
        };
      },
      requestHistoryReadPermission: jest.fn(async () => true),
      readRecords: jest.fn(async (recordType: SupportedHealthConnectRecordType) => {
        expect(recordType).toBe('ExerciseSession');
        events.push('read-history');
        return {
          records: [{
            startTime: '2026-07-14T10:00:00Z',
            endTime: '2026-07-14T11:00:00Z',
            exerciseType: 79,
            metadata: metadata('exercise-present', '2026-07-14T11:01:00Z'),
          }],
        };
      }),
    } as unknown as HealthConnectGateway;
    const tokenStore = {
      loadTokens: jest.fn(async () => tokens),
      saveToken: jest.fn(async (_userId: string, recordType: string, token: string) => {
        events.push(`save:${recordType}:${token}`);
      }),
    } as unknown as HealthConnectTokenStore;
    const importer = {
      listHealthConnectRecordIds: jest.fn(async () => ({
        ids: ['exercise-present', 'exercise-deleted'], nextAfter: null,
      })),
      importWearableRecords: jest.fn(async (
        _syncId: string, recordType: string, _complete: boolean,
        records: { external_record_id: string }[], deletedIds: string[],
      ) => {
        if (recordType === 'exercise') {
          events.push(`import:${records.map((item) => item.external_record_id).join(',')}:${deletedIds.join(',')}`);
        }
      }),
    };

    await syncHealthConnect(
      '019b1111-1111-7111-8111-111111111111',
      gateway,
      tokenStore,
      importer,
    );
    expect(gateway.requestHistoryReadPermission).toHaveBeenCalledTimes(1);
    expect(importer.listHealthConnectRecordIds).toHaveBeenCalledWith('exercise', undefined, undefined);
    expect(events).toEqual(expect.arrayContaining([
      'read-history',
      'import:exercise-present:',
      'import::exercise-deleted',
      'save:ExerciseSession:exercise-final',
    ]));
    expect(events.indexOf('save:ExerciseSession:exercise-final')).toBeGreaterThan(
      events.indexOf('import::exercise-deleted'),
    );
  });

  test('keeps the expired cursor and server records when full history is empty', async () => {
    const gateway = {
      getChanges: jest.fn(async (recordType: SupportedHealthConnectRecordType, token?: string) => ({
        upsertionChanges: [], deletionChanges: [],
        nextChangesToken: `${recordType}-new`,
        changesTokenExpired: recordType === 'ExerciseSession' && token === 'ExerciseSession-token',
        hasMore: false,
      })),
      requestHistoryReadPermission: jest.fn(async () => true),
      readRecords: jest.fn(async () => ({ records: [] })),
    } as unknown as HealthConnectGateway;
    const tokenStore = {
      loadTokens: jest.fn(async () => allTokensExcept()),
      saveToken: jest.fn(),
    } as unknown as HealthConnectTokenStore;
    const importer = {
      importWearableRecords: jest.fn(),
      listHealthConnectRecordIds: jest.fn(async () => ({
        ids: ['existing-exercise'], nextAfter: null,
      })),
    };

    await expect(syncHealthConnect(
      '019b1111-1111-7111-8111-111111111111', gateway, tokenStore, importer,
    )).rejects.toThrow('已保留服务器原始数据');
    expect(tokenStore.saveToken).not.toHaveBeenCalledWith(
      expect.anything(), 'ExerciseSession', expect.anything(),
    );
    expect(importer.importWearableRecords).not.toHaveBeenCalledWith(
      expect.anything(), 'exercise', expect.anything(),
      expect.anything(), expect.arrayContaining(['existing-exercise']), expect.anything(),
    );
  });

  test('does not replace an expired cursor when history access is denied', async () => {
    const gateway = {
      getChanges: jest.fn(async (recordType: SupportedHealthConnectRecordType, token?: string) => ({
        upsertionChanges: [], deletionChanges: [], nextChangesToken: 'expired',
        changesTokenExpired: recordType === 'ExerciseSession' && token === 'ExerciseSession-token',
        hasMore: false,
      })),
      requestHistoryReadPermission: jest.fn(async () => false),
    } as unknown as HealthConnectGateway;
    const tokenStore = {
      loadTokens: jest.fn(async () => allTokensExcept()),
      saveToken: jest.fn(),
    } as unknown as HealthConnectTokenStore;
    const importer = {
      importWearableRecords: jest.fn(), listHealthConnectRecordIds: jest.fn(),
    };

    await expect(syncHealthConnect(
      '019b1111-1111-7111-8111-111111111111', gateway, tokenStore, importer,
    )).rejects.toThrow('请在 Health Connect 授权读取历史健康数据');
    expect(importer.listHealthConnectRecordIds).not.toHaveBeenCalled();
    expect(tokenStore.saveToken).not.toHaveBeenCalledWith(
      expect.anything(), 'ExerciseSession', expect.anything(),
    );
  });

  test('stops before upload when logout cancels an in-flight native read', async () => {
    let resolveChanges!: (value: {
      upsertionChanges: { record: unknown }[];
      deletionChanges: { recordId: string }[];
      nextChangesToken: string;
      changesTokenExpired: boolean;
      hasMore: boolean;
    }) => void;
    const changes = new Promise<Parameters<typeof resolveChanges>[0]>((resolve) => {
      resolveChanges = resolve;
    });
    const gateway = {
      getChanges: jest.fn(() => changes),
    } as unknown as HealthConnectGateway;
    const tokenStore = {
      loadTokens: jest.fn(async () => allTokensExcept()),
      saveToken: jest.fn(),
    } as unknown as HealthConnectTokenStore;
    const importer = { importWearableRecords: jest.fn(), listHealthConnectRecordIds: jest.fn() };
    const controller = new AbortController();

    const pending = syncHealthConnect(
      '019b1111-1111-7111-8111-111111111111',
      gateway,
      tokenStore,
      importer,
      undefined,
      new Date(),
      controller.signal,
    );
    controller.abort(new Error('logout cancelled sync'));
    resolveChanges({
      upsertionChanges: [],
      deletionChanges: [],
      nextChangesToken: 'steps-next',
      changesTokenExpired: false,
      hasMore: false,
    });

    await expect(pending).rejects.toThrow('logout cancelled sync');
    expect(importer.importWearableRecords).not.toHaveBeenCalled();
    expect(tokenStore.saveToken).not.toHaveBeenCalled();
  });
});
