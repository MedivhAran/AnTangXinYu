import { nativeHealthConnect } from './native-health-connect';
import { ZEPP_DATA_ORIGIN } from './health-connect-types';
import {
  getGrantedPermissions,
  requestPermission,
} from 'react-native-health-connect';
import {
  hasHistoryReadPermission,
  isHistoryReadAvailable,
} from '../../modules/health-connect-background';

jest.mock('../../modules/health-connect-background', () => ({
  hasHistoryReadPermission: jest.fn(),
  isHistoryReadAvailable: jest.fn(),
}));

const mockReadRecords = jest.fn(
  async (..._args: unknown[]): Promise<{
    records: unknown[];
    pageToken: string | null;
  }> => ({ records: [], pageToken: null }),
);
const mockGetChanges = jest.fn(async (..._args: unknown[]) => ({
  upsertionChanges: [],
  deletionChanges: [],
  nextChangesToken: 'next',
  changesTokenExpired: false,
  hasMore: false,
}));

jest.mock('react-native-health-connect', () => ({
  getSdkStatus: jest.fn(),
  initialize: jest.fn(),
  requestPermission: jest.fn(),
  getGrantedPermissions: jest.fn(),
  readRecords: (...args: unknown[]) => mockReadRecords(...args),
  getChanges: (...args: unknown[]) => mockGetChanges(...args),
}));

describe('nativeHealthConnect Zepp boundary', () => {
  beforeEach(() => jest.clearAllMocks());

  test('filters initial reads to the Zepp package', async () => {
    const page = await nativeHealthConnect.readRecords(
      'Steps',
      '2026-06-14T00:00:00Z',
      '2026-07-14T00:00:00Z',
      'page-2',
    );

    expect(mockReadRecords).toHaveBeenCalledWith('Steps', {
      timeRangeFilter: {
        operator: 'between',
        startTime: '2026-06-14T00:00:00Z',
        endTime: '2026-07-14T00:00:00Z',
      },
      ascendingOrder: true,
      pageSize: 1000,
      pageToken: 'page-2',
      dataOriginFilter: [ZEPP_DATA_ORIGIN],
    });
    expect(page).toEqual({ records: [] });
  });

  test('keeps a real next-page token', async () => {
    mockReadRecords.mockResolvedValueOnce({
      records: [{ id: 'record-1' }],
      pageToken: 'page-3',
    });

    const page = await nativeHealthConnect.readRecords(
      'Steps',
      '2026-06-14T00:00:00Z',
      '2026-07-14T00:00:00Z',
    );

    expect(page).toEqual({
      records: [{ id: 'record-1' }],
      pageToken: 'page-3',
    });
  });

  test('filters incremental changes to the same Zepp package', async () => {
    await nativeHealthConnect.getChanges('HeartRate', 'heart-token');

    expect(mockGetChanges).toHaveBeenCalledWith({
      recordTypes: ['HeartRate'],
      changesToken: 'heart-token',
      dataOriginFilters: [ZEPP_DATA_ORIGIN],
    });
  });

  test('requests and checks the dedicated background permission', async () => {
    jest.mocked(requestPermission).mockResolvedValueOnce([
      { accessType: 'read', recordType: 'BackgroundAccessPermission' },
    ]);
    jest.mocked(getGrantedPermissions).mockResolvedValueOnce([
      { accessType: 'read', recordType: 'BackgroundAccessPermission' },
    ]);

    await expect(nativeHealthConnect.requestBackgroundPermission()).resolves.toBe(
      true,
    );
    await expect(nativeHealthConnect.hasBackgroundPermission()).resolves.toBe(true);
    expect(requestPermission).toHaveBeenCalledWith([
      { accessType: 'read', recordType: 'BackgroundAccessPermission' },
    ]);
  });

  test('verifies the history grant through the native module after requesting it', async () => {
    jest.mocked(isHistoryReadAvailable).mockResolvedValue(true);
    jest.mocked(hasHistoryReadPermission)
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);

    await expect(nativeHealthConnect.requestHistoryReadPermission()).resolves.toBe(true);
    expect(requestPermission).toHaveBeenCalledWith([
      { accessType: 'read', recordType: 'ReadHealthDataHistory' },
    ]);
    expect(hasHistoryReadPermission).toHaveBeenCalledTimes(2);
  });
});
