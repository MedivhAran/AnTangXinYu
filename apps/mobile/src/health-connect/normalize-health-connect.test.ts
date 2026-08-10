import { normalizeHealthConnectRecord } from './normalize-health-connect';
import type { SupportedHealthConnectRecordType } from './health-connect-types';

const metadata = {
  id: 'zepp-record-1',
  dataOrigin: 'com.huami.watch.hmwatchmanager',
  lastModifiedTime: '2026-07-14T10:10:00Z',
  recordingMethod: 2,
  device: { manufacturer: 'Amazfit', model: 'Active 2', type: 6 },
};
const interval = {
  startTime: '2026-07-14T10:00:00Z',
  endTime: '2026-07-14T10:05:00Z',
  startZoneOffset: { id: '+08:00', totalSeconds: 28800 },
  endZoneOffset: { id: '+08:00', totalSeconds: 28800 },
  metadata,
};
const instant = {
  time: '2026-07-14T10:00:00Z',
  zoneOffset: { id: '+08:00', totalSeconds: 28800 },
  metadata,
};

const cases: [SupportedHealthConnectRecordType, object, string, object][] = [
  ['Steps', { ...interval, count: 321 }, 'steps', { count: 321 }],
  [
    'ExerciseSession',
    {
      ...interval,
      exerciseType: 79,
      title: '散步',
      notes: '不得上传',
      exerciseRoute: { route: [{ latitude: 1, longitude: 2 }] },
    },
    'exercise',
    { exercise_type: 79, title: '散步' },
  ],
  [
    'Distance',
    { ...interval, distance: { inMeters: 1234.5 } },
    'distance',
    { meters: 1234.5 },
  ],
  [
    'ElevationGained',
    { ...interval, elevation: { inMeters: 16 } },
    'elevation_gained',
    { meters: 16 },
  ],
  [
    'Weight',
    { ...instant, weight: { inKilograms: 62.5 } },
    'weight',
    { kilograms: 62.5 },
  ],
  [
    'RespiratoryRate',
    { ...instant, zoneOffset: undefined, rate: 15.5 },
    'respiratory_rate',
    { breaths_per_minute: 15.5 },
  ],
  [
    'RestingHeartRate',
    { ...instant, beatsPerMinute: 64 },
    'resting_heart_rate',
    { beats_per_minute: 64 },
  ],
  [
    'HeartRate',
    {
      ...interval,
      samples: [
        { time: '2026-07-14T10:01:00Z', beatsPerMinute: 82 },
        { time: '2026-07-14T10:00:00Z', beatsPerMinute: 78 },
      ],
    },
    'heart_rate',
    {
      samples: [
        { time: '2026-07-14T10:00:00Z', beats_per_minute: 78 },
        { time: '2026-07-14T10:01:00Z', beats_per_minute: 82 },
      ],
    },
  ],
  [
    'SleepSession',
    {
      ...interval,
      title: '不得上传',
      notes: '不得上传',
      stages: [
        {
          startTime: '2026-07-14T10:00:00Z',
          endTime: '2026-07-14T10:05:00Z',
          stage: 6,
        },
      ],
    },
    'sleep',
    {
      stages: [
        {
          start_time: '2026-07-14T10:00:00Z',
          end_time: '2026-07-14T10:05:00Z',
          stage: 'rem',
        },
      ],
    },
  ],
  [
    'OxygenSaturation',
    { ...instant, percentage: 98 },
    'oxygen_saturation',
    { percentage: 98 },
  ],
];

describe('normalizeHealthConnectRecord', () => {
  test.each(cases)('normalizes %s into the strict API record', (type, input, apiType, data) => {
    const result = normalizeHealthConnectRecord(type, input);

    expect(result).toMatchObject({
      external_record_id: metadata.id,
      record_type: apiType,
      source_package: metadata.dataOrigin,
      source_last_modified_at: metadata.lastModifiedTime,
      recording_method: 2,
      device: {
        manufacturer: 'Amazfit',
        model: 'Active 2',
        device_type: 6,
      },
      data,
    });
    expect(JSON.stringify(result)).not.toContain('不得上传');
    expect(JSON.stringify(result)).not.toContain('exerciseRoute');
  });

  test('normalizes a legal sleep session without stages to an empty array', () => {
    expect(
      normalizeHealthConnectRecord('SleepSession', {
        ...interval,
        stages: undefined,
      }).data,
    ).toEqual({ stages: [] });
  });

  test('fails on an unknown sleep stage instead of guessing', () => {
    expect(() =>
      normalizeHealthConnectRecord('SleepSession', {
        ...interval,
        stages: [
          {
            startTime: interval.startTime,
            endTime: interval.endTime,
            stage: 99,
          },
        ],
      }),
    ).toThrow('未知睡眠阶段：99');
  });

  test('rejects records written by a non-Zepp data origin', () => {
    expect(() =>
      normalizeHealthConnectRecord('Steps', {
        ...interval,
        count: 100,
        metadata: { ...metadata, dataOrigin: 'com.google.android.apps.fitness' },
      }),
    ).toThrow('不支持 Health Connect 数据来源');
  });
});
