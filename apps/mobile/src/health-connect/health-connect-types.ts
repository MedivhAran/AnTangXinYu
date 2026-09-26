import type { Permission, RecordType } from 'react-native-health-connect';

export const ZEPP_DATA_ORIGIN = 'com.huami.watch.hmwatchmanager';

export const healthConnectRecordTypes = [
  'Steps',
  'ExerciseSession',
  'SleepSession',
  'Distance',
  'ElevationGained',
  'Weight',
  'RespiratoryRate',
  'RestingHeartRate',
  'HeartRate',
  'OxygenSaturation',
] as const satisfies readonly RecordType[];

export type SupportedHealthConnectRecordType =
  (typeof healthConnectRecordTypes)[number];

export const healthConnectReadPermissions: Permission[] =
  healthConnectRecordTypes.map((recordType) => ({
    accessType: 'read',
    recordType,
  }));

type HealthConnectChanges = {
  upsertionChanges: { record: unknown }[];
  deletionChanges: { recordId: string }[];
  nextChangesToken: string;
  changesTokenExpired: boolean;
  hasMore: boolean;
};

export interface HealthConnectGateway {
  getSdkStatus(): Promise<number>;
  initialize(): Promise<boolean>;
  requestReadPermissions(): Promise<Permission[]>;
  getGrantedPermissions(): Promise<Permission[]>;
  requestHistoryReadPermission(): Promise<boolean>;
  readRecords(
    recordType: SupportedHealthConnectRecordType,
    startTime: string,
    endTime: string,
    pageToken?: string,
  ): Promise<{ records: unknown[]; pageToken?: string }>;
  getChanges(
    recordType: SupportedHealthConnectRecordType,
    changesToken?: string,
  ): Promise<HealthConnectChanges>;
}

export type HealthConnectChangeTokens = Partial<
  Record<SupportedHealthConnectRecordType, string>
>;

export interface HealthConnectTokenStore {
  getActiveUserId(): Promise<string | null>;
  setActiveUserId(userId: string): Promise<void>;
  loadTokens(userId: string): Promise<HealthConnectChangeTokens>;
  saveToken(
    userId: string,
    recordType: SupportedHealthConnectRecordType,
    token: string,
  ): Promise<void>;
}
