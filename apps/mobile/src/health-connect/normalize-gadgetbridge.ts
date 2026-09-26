import type { WearableRecord } from '../api/types';
import {
  MAX_HEART_RATE_SAMPLES_PER_RECORD,
  type HealthBundle,
  type SleepSession,
} from '../services/GadgetbridgeService';

// =====================================================================
// 把 Gadgetbridge 的 HealthBundle 转成后端 wearable-imports 的强类型请求体。
//
// 这里之所以单独成一个模块，是因为之前直接手搓 fetch 的写法与后端契约完全
// 不一致（实测 HTTP 422）：
//   ✗ client_sync_id 用了 `gb-<时间戳>-<随机>`，后端要求合法 UUID
//   ✗ 发了 userId / source / fileName / totalSteps / avgHeartRate / heartRates …
//     而后端 StrictApiModel 是 extra="forbid"，多一个字段就 422
//   ✗ 缺 record_type 和 records
//   ✗ 一次请求混发多种 record_type，后端要求每次只能一种
//
// 现在的做法：把每类指标转成「一条记录一段区间」的形状，再交给
// ApiClient.importWearableRecords 分批上传（每次 ≤1000 条记录）。
// 这样幂等、可重试、也不会再触发 422。
// =====================================================================

/** 后端 WearableImportRequest.records 的硬上限。 */
export const IMPORT_BATCH_SIZE = 1000;

/**
 * ⚠️ 这里需要你拍板，我不擅自改后端已确认的行为。
 *
 * 后端 api/src/antang_api/schemas/health_profile.py 把来源写死了：
 *     source_package: Literal["com.huami.watch.hmwatchmanager"]
 * 对应 AGENTS.md 里已确认的决定——"首版只接收目标设备实际使用的 Google Play 版
 * Zepp 数据源，这同时避免把手机或其他 App 的累计步数、距离再次相加"。
 *
 * 而 Gadgetbridge 导入的数据，真实来源是
 *     nodomain.freeyourgadget.gadgetbridge
 *
 * 两者只能选一个：
 *   · 填 ZEPP 常量（当前值）：今天就能传通，但库里存的来源是错的，而且
 *     api/src/antang_api/proactive_care/heart_rate_replay.py 的 _ZEPP_PACKAGE
 *     过滤会把它当成 Zepp 数据参与主动关怀。
 *   · 填 GADGETBRIDGE 常量：语义正确，但在后端放开这个 Literal 之前一定 422。
 *
 * 后端改动很小（见 patch），但那属于改动已确认的产品行为，所以我按能跑通的
 * 方式先落地，等你决定后再一行切换。
 */
export const ZEPP_SOURCE_PACKAGE = 'com.huami.watch.hmwatchmanager';
export const GADGETBRIDGE_SOURCE_PACKAGE = 'nodomain.freeyourgadget.gadgetbridge';
export const SOURCE_PACKAGE = ZEPP_SOURCE_PACKAGE;

/** Health Connect 的 RECORDING_METHOD_AUTOMATICALLY_RECORDED，手环自动记录与之语义一致。 */
const RECORDING_METHOD_AUTOMATICALLY_RECORDED = 2;

/** 一个 recordType 对应的一批待上传记录。 */
export type RecordGroup = {
  recordType: WearableRecord['record_type'];
  records: WearableRecord[];
};

/** 一次上传（recordType + 最多 1000 条记录）。 */
export type ImportBatch = {
  recordType: WearableRecord['record_type'];
  clientSyncId: string;
  records: WearableRecord[];
};

/** 该时刻相对 UTC 的偏移秒数，落在后端允许的 ±64800 内。 */
function zoneOffsetSeconds(iso: string): number {
  return -new Date(iso).getTimezoneOffset() * 60;
}

function addMinutes(iso: string, minutes: number): string {
  return new Date(Date.parse(iso) + minutes * 60000).toISOString();
}

function epochSeconds(iso: string): number {
  return Math.floor(Date.parse(iso) / 1000);
}

function base(
  externalRecordId: string,
  startTime: string,
  endTime: string,
  bundle: HealthBundle,
): Omit<WearableRecord, 'record_type' | 'data'> {
  return {
    external_record_id: externalRecordId,
    start_time: startTime,
    end_time: endTime,
    start_zone_offset_seconds: zoneOffsetSeconds(startTime),
    end_zone_offset_seconds: zoneOffsetSeconds(endTime),
    source_package: SOURCE_PACKAGE,
    recording_method: RECORDING_METHOD_AUTOMATICALLY_RECORDED,
    // 设备信息取自导入库的 DEVICE 表，不编造
    device:
      bundle.device === null
        ? null
        : {
            manufacturer: bundle.device.manufacturer,
            model: bundle.device.model,
            // 不猜 Health Connect 的 device_type 枚举，留空由后端按 null 处理
            device_type: null,
          },
    // Gadgetbridge 的分钟数据不会回溯修改，用区间终点做"最后修改时间"，
    // 重复导入同一份文件会得到 records_unchanged 而不是重复记录。
    source_last_modified_at: endTime,
  };
}

/**
 * 同一时间戳只保留一个心率样本。
 * 实测库里存在同一 TIMESTAMP 两行的情况（OTHER_TIMESTAMP 配对行），
 * 而后端不接受重复时间的心率样本，所以这里必须去重。
 */
export function dedupeHeartRates(
  samples: { timestamp: string; heartRate: number }[],
): { timestamp: string; heartRate: number }[] {
  const byTimestamp = new Map<string, number>();
  for (const sample of samples) {
    const existing = byTimestamp.get(sample.timestamp);
    if (existing === undefined || sample.heartRate > existing) {
      byTimestamp.set(sample.timestamp, sample.heartRate);
    }
  }
  return [...byTimestamp.entries()]
    .map(([timestamp, heartRate]) => ({ timestamp, heartRate }))
    .sort((left, right) => left.timestamp.localeCompare(right.timestamp));
}

function heartRateRecords(bundle: HealthBundle): WearableRecord[] {
  const samples = dedupeHeartRates(bundle.heartRates);
  const records: WearableRecord[] = [];
  for (let index = 0; index < samples.length; index += MAX_HEART_RATE_SAMPLES_PER_RECORD) {
    const chunk = samples.slice(index, index + MAX_HEART_RATE_SAMPLES_PER_RECORD);
    const startTime = chunk[0].timestamp;
    const endTime = chunk[chunk.length - 1].timestamp;
    records.push({
      ...base(`gb-hr-${epochSeconds(startTime)}-${epochSeconds(endTime)}`, startTime, endTime, bundle),
      record_type: 'heart_rate',
      data: {
        samples: chunk.map((sample) => ({
          time: sample.timestamp,
          beats_per_minute: sample.heartRate,
        })),
      },
    });
  }
  return records;
}

function restingHeartRateRecords(bundle: HealthBundle): WearableRecord[] {
  return bundle.restingHeartRates.map((sample) => ({
    ...base(`gb-rhr-${epochSeconds(sample.timestamp)}`, sample.timestamp, sample.timestamp, bundle),
    record_type: 'resting_heart_rate' as const,
    data: { beats_per_minute: sample.restingHeartRate },
  }));
}

function oxygenSaturationRecords(bundle: HealthBundle): WearableRecord[] {
  return bundle.spo2.map((sample) => ({
    ...base(`gb-spo2-${epochSeconds(sample.timestamp)}`, sample.timestamp, sample.timestamp, bundle),
    record_type: 'oxygen_saturation' as const,
    data: { percentage: sample.spo2 },
  }));
}

function stepRecords(bundle: HealthBundle): WearableRecord[] {
  return bundle.steps.map((bucket) => {
    const endTime = addMinutes(bucket.timestamp, bundle.bucketMinutes);
    return {
      ...base(`gb-steps-${epochSeconds(bucket.timestamp)}`, bucket.timestamp, endTime, bundle),
      record_type: 'steps' as const,
      data: { count: Math.max(0, Math.round(bucket.steps)) },
    };
  });
}

function distanceRecords(bundle: HealthBundle): WearableRecord[] {
  return bundle.distance.map((bucket) => {
    const endTime = addMinutes(bucket.timestamp, bundle.bucketMinutes);
    return {
      ...base(`gb-distance-${epochSeconds(bucket.timestamp)}`, bucket.timestamp, endTime, bundle),
      record_type: 'distance' as const,
      data: { meters: Math.max(0, bucket.distance) },
    };
  });
}

function sleepRecords(bundle: HealthBundle): WearableRecord[] {
  return bundle.sleep.map((session: SleepSession) => ({
    ...base(`gb-sleep-${epochSeconds(session.startTime)}`, session.startTime, session.endTime, bundle),
    record_type: 'sleep' as const,
    data: {
      stages: session.stages.map((slice) => ({
        start_time: slice.startTime,
        end_time: slice.endTime,
        stage: slice.stage,
      })),
    },
  }));
}

/**
 * 按后端接受的形状分组。
 *
 * 注意：卡路里和压力**不在这里**——后端 WearableRecordType 没有这两种类型
 * （见 api/src/antang_api/models/wearable.py），它们只在看板上本地展示。
 */
export function normalizeGadgetbridgeBundle(bundle: HealthBundle): RecordGroup[] {
  const groups: RecordGroup[] = [
    { recordType: 'heart_rate', records: heartRateRecords(bundle) },
    { recordType: 'steps', records: stepRecords(bundle) },
    { recordType: 'oxygen_saturation', records: oxygenSaturationRecords(bundle) },
    { recordType: 'resting_heart_rate', records: restingHeartRateRecords(bundle) },
    { recordType: 'distance', records: distanceRecords(bundle) },
    { recordType: 'sleep', records: sleepRecords(bundle) },
  ];
  return groups.filter((group) => group.records.length > 0);
}

/**
 * 切成可上传的批次：每批一种 recordType、最多 1000 条记录。
 * clientSyncId 由调用方生成合法 UUID（expo-crypto 的 randomUUID），
 * 同一次批次的网络重试要复用同一个 id。
 */
export function buildImportBatches(
  bundle: HealthBundle,
  createSyncId: () => string,
): ImportBatch[] {
  const batches: ImportBatch[] = [];
  for (const group of normalizeGadgetbridgeBundle(bundle)) {
    for (let index = 0; index < group.records.length; index += IMPORT_BATCH_SIZE) {
      batches.push({
        recordType: group.recordType,
        clientSyncId: createSyncId(),
        records: group.records.slice(index, index + IMPORT_BATCH_SIZE),
      });
    }
  }
  return batches;
}
