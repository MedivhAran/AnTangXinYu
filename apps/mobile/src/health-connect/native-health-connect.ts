import {
  getChanges,
  getGrantedPermissions,
  getSdkStatus,
  initialize,
  readRecords,
  requestPermission,
  type Permission,
} from 'react-native-health-connect';
import { Platform } from 'react-native';

import {
  healthConnectReadPermissions,
  type HealthConnectGateway,
  type SupportedHealthConnectRecordType,
  ZEPP_DATA_ORIGIN,
} from './health-connect-types';

const SDK_AVAILABLE = 3;

export class HealthConnectReadPermissionError extends Error {
  constructor() {
    super('没有获得全部十类 Health Connect 读取权限');
    this.name = 'HealthConnectReadPermissionError';
  }
}

function normalizePageToken(value: unknown): string | undefined {
  // Health Connect uses null to mean that the current page is the last one.
  // react-native-health-connect 3.5.3 forwards that null value even though its
  // public TypeScript type only declares string | undefined.
  if (value === null || value === undefined) return undefined;
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error('Health Connect 返回了无效的分页游标');
  }
  return value;
}

type NativeHealthConnectGateway = HealthConnectGateway & {
  requestBackgroundPermission(): Promise<boolean>;
  hasBackgroundPermission(): Promise<boolean>;
};

/** The only module that talks directly to the native Health Connect package. */
export const nativeHealthConnect: NativeHealthConnectGateway = {
  getSdkStatus,
  initialize,

  async requestReadPermissions() {
    return (await requestPermission(healthConnectReadPermissions)) as Permission[];
  },

  async requestBackgroundPermission() {
    const granted = await requestPermission([
      { accessType: 'read', recordType: 'BackgroundAccessPermission' },
    ]);
    return granted.some(
      (permission) =>
        permission.accessType === 'read' &&
        permission.recordType === 'BackgroundAccessPermission',
    );
  },

  async hasBackgroundPermission() {
    const granted = await getGrantedPermissions();
    return granted.some(
      (permission) =>
        permission.accessType === 'read' &&
        permission.recordType === 'BackgroundAccessPermission',
    );
  },

  async getGrantedPermissions() {
    return (await getGrantedPermissions()) as Permission[];
  },

  async readRecords(
    recordType: SupportedHealthConnectRecordType,
    startTime: string,
    endTime: string,
    pageToken?: string,
  ) {
    const response = await readRecords(recordType, {
      timeRangeFilter: { operator: 'between', startTime, endTime },
      ascendingOrder: true,
      pageSize: 1000,
      dataOriginFilter: [ZEPP_DATA_ORIGIN],
      ...(pageToken === undefined ? {} : { pageToken }),
    });
    const normalizedPageToken = normalizePageToken(
      (response as { pageToken?: unknown }).pageToken,
    );
    return {
      records: response.records,
      ...(normalizedPageToken === undefined
        ? {}
        : { pageToken: normalizedPageToken }),
    };
  },

  async getChanges(recordType, changesToken) {
    // react-native-health-connect 3.5.3 的公开 TS 类型误写成了
    // dataOriginFilter，但该版本 Android 原生代码实际读取复数键
    // dataOriginFilters。依赖已精确锁定，这里按真实原生合同传值；
    // 升级依赖时必须重新核对并删除这层版本兼容说明。
    const nativeRequest = {
      recordTypes: [recordType],
      dataOriginFilters: [ZEPP_DATA_ORIGIN],
      ...(changesToken === undefined ? {} : { changesToken }),
    } as unknown as Parameters<typeof getChanges>[0];
    return getChanges(nativeRequest);
  },
};

export async function prepareHealthConnect(
  gateway: HealthConnectGateway,
  requestPermissions: boolean,
): Promise<void> {
  if (Platform.OS !== 'android') {
    throw new Error('Health Connect 首版只支持 Android');
  }
  const sdkStatus = await gateway.getSdkStatus();
  if (sdkStatus !== SDK_AVAILABLE) {
    throw new Error(
      sdkStatus === 2
        ? 'Health Connect 需要更新后才能使用'
        : '这台设备不支持 Health Connect',
    );
  }
  if (!(await gateway.initialize())) {
    throw new Error('Health Connect 初始化失败');
  }
  if (requestPermissions) await gateway.requestReadPermissions();
  const grantedRecordTypes = new Set(
    (await gateway.getGrantedPermissions())
      .filter((permission) => permission.accessType === 'read')
      .map((permission) => permission.recordType),
  );
  if (
    !healthConnectReadPermissions.every((permission) =>
      grantedRecordTypes.has(permission.recordType),
    )
  ) {
    throw new HealthConnectReadPermissionError();
  }
}
