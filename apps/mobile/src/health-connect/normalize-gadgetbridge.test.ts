import {
  buildImportBatches,
  GADGETBRIDGE_SOURCE_PACKAGE,
  normalizeGadgetbridgeBundle,
} from './normalize-gadgetbridge';
import type { HealthBundle, SleepSession } from '../services/GadgetbridgeService';

// 后端 WearableImportRequest 是 StrictApiModel(extra="forbid")，
// 多一个字段就 422。之前手搓 payload 正是栽在这里（实测 body.userId /
// body.source / body.heartRates … 全部 "Extra inputs are not permitted"）。
// 所以这里断言每条记录的键集合必须**精确等于**后端接受的集合。
const BASE_KEYS = [
  'external_record_id',
  'record_type',
  'start_time',
  'end_time',
  'start_zone_offset_seconds',
  'end_zone_offset_seconds',
  'source_package',
  'recording_method',
  'device',
  'source_last_modified_at',
  'data',
].sort();

function sleepSession(): SleepSession {
  return {
    startTime: '2026-09-06T00:00:00.000Z',
    endTime: '2026-09-06T07:00:00.000Z',
    stages: [
      { startTime: '2026-09-06T00:00:00.000Z', endTime: '2026-09-06T00:20:00.000Z', stage: 'awake' },
      { startTime: '2026-09-06T00:20:00.000Z', endTime: '2026-09-06T02:00:00.000Z', stage: 'deep' },
      { startTime: '2026-09-06T02:00:00.000Z', endTime: '2026-09-06T07:00:00.000Z', stage: 'light' },
    ],
    score: 86,
    deepMinutes: 100,
    efficiency: 94,
    minOxygenSaturation: 91,
    minHeartRate: 53,
    maxHeartRate: 79,
  };
}

function bundle(overrides: Partial<HealthBundle> = {}): HealthBundle {
  return {
    version: 2,
    bucketMinutes: 15,
    fileName: 'gadgetbridge.db',
    exportModifiedAt: '2026-09-26T12:00:00.000Z',
    heartRates: [
      // 故意乱序，并让同一时间戳出现两次（真实库里 OTHER_TIMESTAMP 配对行就是这样）
      { timestamp: '2026-09-06T01:00:00.000Z', heartRate: 70 },
      { timestamp: '2026-09-06T00:00:00.000Z', heartRate: 61 },
      { timestamp: '2026-09-06T01:00:00.000Z', heartRate: 66 },
    ],
    steps: [{ timestamp: '2026-09-06T00:00:00.000Z', steps: 132 }],
    spo2: [{ timestamp: '2026-09-06T03:00:00.000Z', spo2: 97 }],
    restingHeartRates: [{ timestamp: '2026-09-06T02:00:00.000Z', restingHeartRate: 54 }],
    calories: [{ timestamp: '2026-09-06T00:00:00.000Z', calories: 12.5 }],
    distance: [{ timestamp: '2026-09-06T00:00:00.000Z', distance: 96.4 }],
    sleep: [sleepSession()],
    stress: [{ timestamp: '2026-09-06T05:00:00.000Z', stress: 24 }],
    daily: [],
    device: { manufacturer: 'Huawei', model: 'Kimi-B19FB', name: 'HUAWEI Band 9-598' },
    totals: {
      rangeStart: '2026-09-06T00:00:00.000Z',
      rangeEnd: '2026-09-06T07:00:00.000Z',
      dayCount: 1,
      heartRateCount: 2,
      heartRateMin: 61,
      heartRateMax: 70,
      heartRateAvg: 66,
      spo2Count: 1,
      spo2Min: 97,
      spo2Avg: 97,
      restingHeartRateCount: 1,
      restingHeartRateMin: 54,
      stepBucketCount: 1,
      totalSteps: 132,
      totalDistanceMeters: 96,
      totalCaloriesKcal: 13,
      sleepSessionCount: 1,
      sleepMinutes: 400,
      stressCount: 1,
      stressAvg: 24,
    },
    ...overrides,
  };
}

describe('normalizeGadgetbridgeBundle', () => {
  it('每条记录的字段集合与后端严格契约一致（多一个字段就会 422）', () => {
    const groups = normalizeGadgetbridgeBundle(bundle());
    expect(groups.length).toBeGreaterThan(0);
    for (const group of groups) {
      for (const record of group.records) {
        expect(Object.keys(record).sort()).toEqual(BASE_KEYS);
        expect(record.record_type).toBe(group.recordType);
        expect(record.source_last_modified_at).toBe('2026-09-26T12:00:00.000Z');
      }
    }
  });

  it('所有记录都如实标注 Gadgetbridge 为数据来源', () => {
    const groups = normalizeGadgetbridgeBundle(bundle());
    expect(groups.length).toBeGreaterThan(0);
    for (const group of groups) {
      for (const record of group.records) {
        expect(record.source_package).toBe(GADGETBRIDGE_SOURCE_PACKAGE);
      }
    }
  });

  it('只为后端支持的 record_type 生成记录：卡路里和压力不参与上传', () => {
    const types = normalizeGadgetbridgeBundle(bundle()).map((group) => group.recordType);
    expect(types.sort()).toEqual(
      ['distance', 'heart_rate', 'oxygen_saturation', 'resting_heart_rate', 'sleep', 'steps'].sort(),
    );
    expect(types).not.toContain('calories');
    expect(types).not.toContain('stress');
  });

  it('心率一条样本一条记录：去重、按时序排列，重复时间戳保留较大值', () => {
    const group = normalizeGadgetbridgeBundle(bundle()).find(
      (item) => item.recordType === 'heart_rate',
    );
    expect(group).toBeDefined();
    expect(group!.records.length).toBe(2);
    expect(group!.records.map((record) => record.external_record_id)).toEqual([
      `gb-hr-${Date.parse('2026-09-06T00:00:00.000Z') / 1000}`,
      `gb-hr-${Date.parse('2026-09-06T01:00:00.000Z') / 1000}`,
    ]);

    const samples = group!.records.map((record) => {
      const data = record.data as { samples: { time: string; beats_per_minute: number }[] };
      expect(data.samples.length).toBe(1);
      expect(record.start_time).toBe(record.end_time);
      expect(record.start_time).toBe(data.samples[0].time);
      return data.samples[0];
    });

    expect(samples.map((sample) => sample.time)).toEqual([
      '2026-09-06T00:00:00.000Z',
      '2026-09-06T01:00:00.000Z',
    ]);
    // 同一时间戳保留较大的有效值
    expect(samples[1].beats_per_minute).toBe(70);
  });

  it('心率不会跨天堆积成单条超大记录（后端读最新记录时有 20000 字符上限）', () => {
    const many = bundle({
      heartRates: Array.from({ length: 2400 }, (_, index) => ({
        timestamp: new Date(Date.UTC(2026, 8, 6) + index * 8 * 60 * 1000).toISOString(),
        heartRate: 60 + (index % 40),
      })),
    });
    const records = normalizeGadgetbridgeBundle(many).find(
      (item) => item.recordType === 'heart_rate',
    )!.records;

    expect(records.length).toBe(2400);
    expect(new Set(records.map((record) => record.external_record_id)).size).toBe(2400);
    for (const record of records) {
      expect(JSON.stringify(record.data).length).toBeLessThan(200);
    }
  });

  it('睡眠阶段有序、不重叠，且落在记录区间内', () => {
    const group = normalizeGadgetbridgeBundle(bundle()).find(
      (item) => item.recordType === 'sleep',
    );
    const record = group!.records[0];
    const stages = (record.data as { stages: { start_time: string; end_time: string }[] })
      .stages;
    expect(stages.length).toBe(3);
    let previousEnd = record.start_time;
    for (const stage of stages) {
      expect(stage.start_time >= previousEnd).toBe(true);
      expect(stage.end_time > stage.start_time).toBe(true);
      expect(stage.start_time >= record.start_time).toBe(true);
      expect(stage.end_time <= record.end_time).toBe(true);
      previousEnd = stage.end_time;
    }
  });

  it('血氧百分比在 0~100 之间，步数是非负整数', () => {
    const groups = normalizeGadgetbridgeBundle(bundle());
    const spo2 = groups.find((item) => item.recordType === 'oxygen_saturation')!.records[0];
    const percentage = (spo2.data as { percentage: number }).percentage;
    expect(percentage).toBeGreaterThanOrEqual(0);
    expect(percentage).toBeLessThanOrEqual(100);

    const steps = groups.find((item) => item.recordType === 'steps')!.records[0];
    const count = (steps.data as { count: number }).count;
    expect(Number.isInteger(count)).toBe(true);
    expect(count).toBeGreaterThanOrEqual(0);
  });

  it('步数记录的区间等于分桶宽度', () => {
    const steps = normalizeGadgetbridgeBundle(bundle()).find(
      (item) => item.recordType === 'steps',
    )!.records[0];
    const minutes =
      (Date.parse(steps.end_time) - Date.parse(steps.start_time)) / 60000;
    expect(minutes).toBe(15);
  });

  it('空 bundle 不生成任何分组', () => {
    const groups = normalizeGadgetbridgeBundle(
      bundle({
        heartRates: [],
        steps: [],
        spo2: [],
        restingHeartRates: [],
        calories: [],
        distance: [],
        sleep: [],
        stress: [],
      }),
    );
    expect(groups).toEqual([]);
  });
});

describe('buildImportBatches', () => {
  it('每批只含一种 record_type，且每批不超过 1000 条记录', () => {
    const many = bundle({
      steps: Array.from({ length: 2500 }, (_, index) => ({
        timestamp: new Date(Date.UTC(2026, 8, 6, 0, index * 15)).toISOString(),
        steps: 10,
      })),
    });
    const batches = buildImportBatches(many, () => 'sync-id');

    for (const batch of batches) {
      expect(batch.records.length).toBeLessThanOrEqual(1000);
      for (const record of batch.records) {
        expect(record.record_type).toBe(batch.recordType);
      }
    }
    const stepBatches = batches.filter((batch) => batch.recordType === 'steps');
    expect(stepBatches.map((batch) => batch.records.length)).toEqual([1000, 1000, 500]);
  });

  it('每次调用都取一个新的 client_sync_id（后端要求合法 UUID）', () => {
    let counter = 0;
    const batches = buildImportBatches(bundle(), () => `uuid-${(counter += 1)}`);
    const ids = batches.map((batch) => batch.clientSyncId);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
