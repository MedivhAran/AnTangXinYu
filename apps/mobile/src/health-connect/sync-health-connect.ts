import { randomUUID } from 'expo-crypto';

import type { WearableRecord } from '../api/types';
import {
  healthConnectRecordTypes,
  type HealthConnectGateway,
  type HealthConnectTokenStore,
  type SupportedHealthConnectRecordType,
} from './health-connect-types';
import { normalizeHealthConnectRecord } from './normalize-health-connect';

const INITIAL_SYNC_DAYS = 30;
const IMPORT_LIMIT = 1000;

export class HealthConnectTokenExpiredError extends Error {
  constructor(
    readonly recordType: SupportedHealthConnectRecordType,
    initial = false,
  ) {
    super(
      initial
        ? `Health Connect ${recordType} 初始游标已失效`
        : `Health Connect ${recordType} 变更游标已失效，当前版本不能安全重建这段历史数据`,
    );
    this.name = 'HealthConnectTokenExpiredError';
  }
}

export interface WearableImporter {
  importWearableRecords(
    clientSyncId: string,
    recordType: WearableRecord['record_type'],
    healthContextComplete: boolean,
    records: WearableRecord[],
    deletedRecordIds: string[],
    signal?: AbortSignal,
  ): Promise<unknown>;
}

type HealthConnectSyncProgress = {
  recordType: SupportedHealthConnectRecordType;
  completedTypes: number;
  totalTypes: number;
};

const wearableRecordType: Record<
  SupportedHealthConnectRecordType,
  WearableRecord['record_type']
> = {
  Steps: 'steps',
  ExerciseSession: 'exercise',
  SleepSession: 'sleep',
  Distance: 'distance',
  ElevationGained: 'elevation_gained',
  Weight: 'weight',
  RespiratoryRate: 'respiratory_rate',
  RestingHeartRate: 'resting_heart_rate',
  HeartRate: 'heart_rate',
  OxygenSaturation: 'oxygen_saturation',
};

function throwIfCancelled(signal?: AbortSignal): void {
  if (!signal?.aborted) return;
  throw signal.reason instanceof Error
    ? signal.reason
    : new Error('手环同步已取消');
}

function mergeNewestRecord(
  records: Map<string, WearableRecord>,
  record: WearableRecord,
): void {
  const existing = records.get(record.external_record_id);
  if (existing === undefined) {
    records.set(record.external_record_id, record);
    return;
  }
  if (JSON.stringify(existing) === JSON.stringify(record)) return;

  const existingModifiedAt = Date.parse(existing.source_last_modified_at);
  const nextModifiedAt = Date.parse(record.source_last_modified_at);
  if (nextModifiedAt > existingModifiedAt) {
    records.set(record.external_record_id, record);
    return;
  }
  if (nextModifiedAt === existingModifiedAt) {
    throw new Error(
      `Health Connect 同一记录 ${record.external_record_id} 在相同修改时间返回了不同内容`,
    );
  }
}

function normalizeChanges(
  recordType: SupportedHealthConnectRecordType,
  upsertions: { record: unknown }[],
  deletions: { recordId: string }[],
): { records: WearableRecord[]; deletedRecordIds: string[] } {
  const records = new Map<string, WearableRecord>();
  for (const change of upsertions) {
    mergeNewestRecord(
      records,
      normalizeHealthConnectRecord(recordType, change.record),
    );
  }

  const deletedRecordIds = new Set<string>();
  for (const change of deletions) {
    if (typeof change.recordId !== 'string' || change.recordId.length === 0) {
      throw new Error('Health Connect 返回了无效的删除记录 ID');
    }
    records.delete(change.recordId);
    deletedRecordIds.add(change.recordId);
  }
  return { records: [...records.values()], deletedRecordIds: [...deletedRecordIds] };
}

async function importInBatches(
  importer: WearableImporter,
  recordType: SupportedHealthConnectRecordType,
  healthContextComplete: boolean,
  records: WearableRecord[],
  deletedRecordIds: string[],
  signal?: AbortSignal,
): Promise<void> {
  const batchCount = Math.max(
    1,
    Math.ceil(records.length / IMPORT_LIMIT),
    Math.ceil(deletedRecordIds.length / IMPORT_LIMIT),
  );
  for (let index = 0; index < batchCount; index += 1) {
    throwIfCancelled(signal);
    await importer.importWearableRecords(
      randomUUID(),
      wearableRecordType[recordType],
      healthContextComplete,
      records.slice(index * IMPORT_LIMIT, (index + 1) * IMPORT_LIMIT),
      deletedRecordIds.slice(index * IMPORT_LIMIT, (index + 1) * IMPORT_LIMIT),
      signal,
    );
  }
}

async function readInitialRecords(
  gateway: HealthConnectGateway,
  recordType: SupportedHealthConnectRecordType,
  startTime: string,
  endTime: string,
  signal?: AbortSignal,
): Promise<WearableRecord[]> {
  const records = new Map<string, WearableRecord>();
  let pageToken: string | undefined;
  do {
    throwIfCancelled(signal);
    const page = await gateway.readRecords(
      recordType,
      startTime,
      endTime,
      pageToken,
    );
    throwIfCancelled(signal);
    for (const value of page.records) {
      mergeNewestRecord(
        records,
        normalizeHealthConnectRecord(recordType, value),
      );
    }
    if (page.pageToken !== undefined && page.pageToken === pageToken) {
      throw new Error(`Health Connect ${recordType} 返回了重复分页游标`);
    }
    pageToken = page.pageToken;
  } while (pageToken !== undefined);
  return [...records.values()];
}

async function initialSyncType(
  userId: string,
  recordType: SupportedHealthConnectRecordType,
  gateway: HealthConnectGateway,
  tokenStore: HealthConnectTokenStore,
  importer: WearableImporter,
  healthContextComplete: boolean,
  now: Date,
  signal?: AbortSignal,
): Promise<void> {
  // With no token, the native call creates a baseline token and immediately
  // returns its first page. We keep that page, read the 30-day snapshot, then
  // continue from the returned token so changes during the read are included.
  let response = await gateway.getChanges(recordType);
  throwIfCancelled(signal);
  if (response.changesTokenExpired) {
    throw new HealthConnectTokenExpiredError(recordType, true);
  }

  const endTime = now.toISOString();
  const startTime = new Date(
    now.getTime() - INITIAL_SYNC_DAYS * 24 * 60 * 60 * 1000,
  ).toISOString();
  const initialRecords = await readInitialRecords(
    gateway,
    recordType,
    startTime,
    endTime,
    signal,
  );
  const records = new Map<string, WearableRecord>();
  const deletedRecordIds = new Set<string>();
  for (const record of initialRecords) mergeNewestRecord(records, record);

  let fetchedAfterInitialRead = false;
  while (true) {
    for (const change of response.upsertionChanges) {
      const record = normalizeHealthConnectRecord(recordType, change.record);
      deletedRecordIds.delete(record.external_record_id);
      mergeNewestRecord(records, record);
    }
    for (const change of response.deletionChanges) {
      if (typeof change.recordId !== 'string' || change.recordId.length === 0) {
        throw new Error('Health Connect 返回了无效的删除记录 ID');
      }
      records.delete(change.recordId);
      deletedRecordIds.add(change.recordId);
    }

    if (!response.hasMore && fetchedAfterInitialRead) break;
    const previousToken = response.nextChangesToken;
    throwIfCancelled(signal);
    response = await gateway.getChanges(recordType, previousToken);
    throwIfCancelled(signal);
    fetchedAfterInitialRead = true;
    if (response.changesTokenExpired) {
      throw new HealthConnectTokenExpiredError(recordType, true);
    }
    if (response.nextChangesToken === previousToken && response.hasMore) {
      throw new Error(`Health Connect ${recordType} 返回了重复变更游标`);
    }
  }

  await importInBatches(
    importer,
    recordType,
    healthContextComplete,
    [...records.values()],
    [...deletedRecordIds],
    signal,
  );
  throwIfCancelled(signal);
  await tokenStore.saveToken(userId, recordType, response.nextChangesToken);
}

async function incrementalSyncType(
  userId: string,
  recordType: SupportedHealthConnectRecordType,
  token: string,
  gateway: HealthConnectGateway,
  tokenStore: HealthConnectTokenStore,
  importer: WearableImporter,
  healthContextComplete: boolean,
  signal?: AbortSignal,
): Promise<void> {
  let currentToken = token;
  while (true) {
    throwIfCancelled(signal);
    const response = await gateway.getChanges(recordType, currentToken);
    throwIfCancelled(signal);
    if (response.changesTokenExpired) {
      throw new HealthConnectTokenExpiredError(recordType);
    }
    if (response.nextChangesToken === currentToken && response.hasMore) {
      throw new Error(`Health Connect ${recordType} 返回了重复变更游标`);
    }
    const changes = normalizeChanges(
      recordType,
      response.upsertionChanges,
      response.deletionChanges,
    );
    await importInBatches(
      importer,
      recordType,
      healthContextComplete,
      changes.records,
      changes.deletedRecordIds,
      signal,
    );
    throwIfCancelled(signal);
    // The local token moves only after every server import in this page succeeds.
    await tokenStore.saveToken(userId, recordType, response.nextChangesToken);
    if (!response.hasMore) return;
    currentToken = response.nextChangesToken;
  }
}

/** Syncs each record type independently. Failures are surfaced and never retried here. */
export async function syncHealthConnect(
  userId: string,
  gateway: HealthConnectGateway,
  tokenStore: HealthConnectTokenStore,
  importer: WearableImporter,
  onProgress?: (progress: HealthConnectSyncProgress) => void,
  now = new Date(),
  signal?: AbortSignal,
): Promise<void> {
  const tokens = await tokenStore.loadTokens(userId);
  const completedContextTypes = new Set<SupportedHealthConnectRecordType>();
  for (const [index, recordType] of healthConnectRecordTypes.entries()) {
    throwIfCancelled(signal);
    onProgress?.({
      recordType,
      completedTypes: index,
      totalTypes: healthConnectRecordTypes.length,
    });
    const healthContextComplete =
      recordType === 'HeartRate' &&
      completedContextTypes.has('Steps') &&
      completedContextTypes.has('ExerciseSession') &&
      completedContextTypes.has('SleepSession');
    const token = tokens[recordType];
    if (token === undefined) {
      await initialSyncType(
        userId,
        recordType,
        gateway,
        tokenStore,
        importer,
        healthContextComplete,
        now,
        signal,
      );
    } else {
      await incrementalSyncType(
        userId,
        recordType,
        token,
        gateway,
        tokenStore,
        importer,
        healthContextComplete,
        signal,
      );
    }
    completedContextTypes.add(recordType);
  }
}

let activeSync:
  | { userId: string; promise: Promise<void> }
  | null = null;

/** Foreground and background callers share the same native cursor advancement. */
export function runHealthConnectSync(
  userId: string,
  gateway: HealthConnectGateway,
  tokenStore: HealthConnectTokenStore,
  importer: WearableImporter,
  onProgress?: (progress: HealthConnectSyncProgress) => void,
  now = new Date(),
  signal?: AbortSignal,
): Promise<void> {
  if (activeSync !== null) {
    if (activeSync.userId !== userId) {
      throw new Error('另一个账号的 Health Connect 同步仍在运行');
    }
    return activeSync.promise;
  }

  let request: Promise<void>;
  request = syncHealthConnect(
    userId,
    gateway,
    tokenStore,
    importer,
    onProgress,
    now,
    signal,
  ).finally(() => {
    if (activeSync?.promise === request) activeSync = null;
  });
  activeSync = { userId, promise: request };
  return request;
}
