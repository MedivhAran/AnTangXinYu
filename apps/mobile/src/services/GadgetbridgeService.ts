import * as FileSystem from 'expo-file-system/legacy';
import * as DocumentPicker from 'expo-document-picker';
import * as SQLite from 'expo-sqlite';

// =====================================================================
// Gadgetbridge 手环数据导入（华为 Band 9 实测校准）
//
// 下面每个取值都来自真实导出库（2.3MB / 170 张表 / 活动表 38920 行），
// 改之前请先读完，避免又走回"按数值范围猜列"的老路。
//
// 1) 华为活动表是 HUAWEI_ACTIVITY_SAMPLE，实测列：
//      TIMESTAMP, DEVICE_ID, USER_ID, OTHER_TIMESTAMP, SOURCE, RAW_KIND,
//      RAW_INTENSITY, STEPS, CALORIES, DISTANCE, SPO, HEART_RATE,
//      RESTING_HEART_RATE
//    没有数据的位置一律写 -1。TIMESTAMP 单位是秒（个别机型给毫秒）。
//
// 2) **这张表没有睡眠列，也没有压力列。** 睡眠阶段在
//    HUAWEI_SLEEP_STAGE_SAMPLE，每晚统计在 HUAWEI_SLEEP_STATS_SAMPLE，
//    压力在 HUAWEI_STRESS_SAMPLE。含 SLEEP/DEEP_SLEEP/REM_SLEEP 三列的是
//    HUAMI_EXTENDED_ACTIVITY_SAMPLE（小米手环用），华为机型里 0 行。
//
// 3) 单位实测：DISTANCE 是米（0.730 米/步），CALORIES 是小卡（要 /1000），
//    SPO 是 0~100 的百分比。
//
// 4) 一次导出 4 万行是常态，所以聚合一律放在 SQL 里做。
//    心率/血氧/静息心率是点测量，保留原始样本；步数/距离/卡路里是区间
//    累加量，按 15 分钟分桶。静息心率按小时取最小值（它的语义就是时段最低值）。
// =====================================================================

/** 步数/距离/卡路里的分桶粒度（分钟）。 */
export const BUCKET_MINUTES = 15;

/** 后端 HeartRateData.samples 的上限（api/.../schemas/health_profile.py）。 */
export const MAX_HEART_RATE_SAMPLES_PER_RECORD = 10000;

// ===== 类型 =====
export type HeartRateRecord = { timestamp: string; heartRate: number };
export type StepRecord = { timestamp: string; steps: number };
export type Spo2Record = { timestamp: string; spo2: number };
export type RestingHrRecord = { timestamp: string; restingHeartRate: number };
export type CaloriesRecord = { timestamp: string; calories: number };
export type DistanceRecord = { timestamp: string; distance: number };

/** 与后端 SleepStage.stage 的枚举一一对应。 */
export type SleepStageName =
  | 'unknown'
  | 'awake'
  | 'sleeping'
  | 'out_of_bed'
  | 'awake_in_bed'
  | 'light'
  | 'deep'
  | 'rem';

export type SleepStageSlice = {
  startTime: string;
  endTime: string;
  stage: SleepStageName;
};

export type SleepSession = {
  startTime: string;
  endTime: string;
  stages: SleepStageSlice[];
  /** 厂商睡眠评分，缺失为 null。以下带 score 前缀的都是厂商字段，仅用于展示。 */
  score: number | null;
  deepMinutes: number | null;
  efficiency: number | null;
  minOxygenSaturation: number | null;
  minHeartRate: number | null;
  maxHeartRate: number | null;
};

export type StressRecord = { timestamp: string; stress: number };

/** 单日汇总，口径是全量数据（不是"最近 N 条"）。 */
export type DailySummary = {
  dayStart: string;
  steps: number;
  distanceMeters: number;
  caloriesKcal: number;
  heartRateMin: number | null;
  heartRateMax: number | null;
  heartRateAvg: number | null;
  spo2Min: number | null;
  spo2Avg: number | null;
  restingHeartRateMin: number | null;
};

export type HealthTotals = {
  rangeStart: string | null;
  rangeEnd: string | null;
  dayCount: number;
  heartRateCount: number;
  heartRateMin: number | null;
  heartRateMax: number | null;
  heartRateAvg: number | null;
  spo2Count: number;
  spo2Min: number | null;
  spo2Avg: number | null;
  restingHeartRateCount: number;
  restingHeartRateMin: number | null;
  stepBucketCount: number;
  totalSteps: number;
  totalDistanceMeters: number;
  totalCaloriesKcal: number;
  sleepSessionCount: number;
  sleepMinutes: number;
  stressCount: number;
  stressAvg: number | null;
};

export type DeviceInfo = {
  manufacturer: string | null;
  model: string | null;
  name: string | null;
};

export type HealthBundle = {
  /** 结构版本，改字段时递增。 */
  version: 2;
  bucketMinutes: number;
  fileName: string;
  heartRates: HeartRateRecord[];
  steps: StepRecord[];
  spo2: Spo2Record[];
  restingHeartRates: RestingHrRecord[];
  /** 千卡（原始小卡已换算）。 */
  calories: CaloriesRecord[];
  /** 米。 */
  distance: DistanceRecord[];
  sleep: SleepSession[];
  stress: StressRecord[];
  daily: DailySummary[];
  device: DeviceInfo | null;
  totals: HealthTotals;
};

export type ParsedHealth = {
  success: boolean;
  message: string;
  fileName?: string;
  bundle?: HealthBundle;
};

// =====================================================================
// 华为睡眠阶段编码 -> 后端枚举
//
// 这是一次**实测推断**，依据是对 6 个完整夜晚的交叉验证：
//
//   1. SLEEP_LATENCY（躺下后清醒的分钟数）应当等于每个夜晚开头 awake 段的
//      长度。实测开头首段 STAGE=4 的长度 **6/6 夜完全相等**
//      （27=27 / 37=37 / 8=8 / 7=7 / 14=14 / 12=12），且 STAGE=4 的时间质心
//      是 0.06（89% 落在夜晚前 1/3）=> 4 是 awake。
//   2. STAGE=2 的分钟数是唯一贴合厂商 DEEP_PART 的（6 夜平均偏差 18.5 分钟，
//      STAGE=1 / STAGE=3 分别偏差 138 / 69 分钟）=> 2 是 deep。
//   3. 余下占比 STAGE=1 = 45.1%、STAGE=3 = 30.6%，符合"浅睡最多、REM 次之"，
//      且 STAGE=3 质心最靠后（后 1/3 占 43%）=> 1 是 light、3 是 rem。
//   4. STAGE=5 全库只有一段 89 分钟且发生在白天（09-09 10:33→09-10 01:58，
//      对应那条 SLEEP_SCORE=-1 的残缺夜），判定为小睡 => 用通用值 sleeping。
//
// 换机型后请对着 Gadgetbridge 的睡眠图复验这张表。
// =====================================================================
export const HUAWEI_SLEEP_STAGE_MAP: Record<number, SleepStageName> = {
  1: 'light',
  2: 'deep',
  3: 'rem',
  4: 'awake',
  5: 'sleeping',
};

/** 相邻阶段采样点间隔超过这个值就把睡眠断成两段（秒）。 */
const SLEEP_SESSION_GAP_SECONDS = 30 * 60;

/** 阶段数据与当晚统计行允许的最大错位（秒）。 */
const SLEEP_STATS_MATCH_WINDOW_SECONDS = 90 * 60;

/** 阶段采样点代表"这一分钟的起点"，最后一段的终点要补 1 分钟。 */
const SLEEP_SAMPLE_SECONDS = 60;

// ===== 工具 =====

/** Gadgetbridge 的 TIMESTAMP 秒/毫秒混用，统一转 ISO。 */
export function toIso(timestamp: number | string): string {
  const value =
    typeof timestamp === 'string' ? Number.parseInt(timestamp, 10) : timestamp;
  if (!Number.isFinite(value) || value <= 0) return '';
  const ms = value > 1e12 ? value : value * 1000;
  const date = new Date(ms);
  return Number.isNaN(date.getTime()) ? '' : date.toISOString();
}

function numberOrNull(value: unknown): number | null {
  const parsed = typeof value === 'string' ? Number(value) : value;
  return typeof parsed === 'number' && Number.isFinite(parsed) ? parsed : null;
}

function positiveOrNull(value: unknown): number | null {
  const parsed = numberOrNull(value);
  return parsed !== null && parsed > 0 ? parsed : null;
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

async function listTables(db: SQLite.SQLiteDatabase): Promise<string[]> {
  const rows = await db.getAllAsync<{ name: string }>(
    `SELECT name FROM sqlite_master WHERE type='table'`,
  );
  return rows.map((row) => row.name);
}

async function tableColumns(
  db: SQLite.SQLiteDatabase,
  table: string,
): Promise<Set<string>> {
  const rows = await db.getAllAsync<{ name: string }>(
    `PRAGMA table_info(${table})`,
  );
  return new Set(rows.map((row) => row.name.toUpperCase()));
}

/** 找活动数据表。华为优先，其次任何带 TIMESTAMP 与心率或步数的表。 */
async function findActivityTable(
  db: SQLite.SQLiteDatabase,
  tables: string[],
): Promise<string | null> {
  const preferred = tables.find((name) => /HUAWEI_ACTIVITY_SAMPLE/i.test(name));
  if (preferred !== undefined) return preferred;

  for (const name of tables) {
    const columns = await tableColumns(db, name);
    if (
      columns.has('TIMESTAMP') &&
      (columns.has('HEART_RATE') || columns.has('STEPS'))
    ) {
      return name;
    }
  }
  return null;
}

/** 找第一张表名匹配且真的含指定列的表。 */
async function findTable(
  db: SQLite.SQLiteDatabase,
  tables: string[],
  pattern: RegExp,
  requiredColumn: string,
): Promise<string | null> {
  for (const name of tables) {
    if (!pattern.test(name)) continue;
    if ((await tableColumns(db, name)).has(requiredColumn)) return name;
  }
  return null;
}

// ===== 选文件 =====
export async function pickHealthFile(): Promise<{
  success: boolean;
  message: string;
  uri?: string;
  fileName?: string;
}> {
  const result = await DocumentPicker.getDocumentAsync({
    type: '*/*',
    copyToCacheDirectory: true,
    multiple: false,
  });

  if (result.canceled || !result.assets?.length) {
    return { success: false, message: '未选择文件' };
  }

  const file = result.assets[0];
  return {
    success: true,
    message: '已选择',
    uri: file.uri,
    fileName: file.name || 'gadgetbridge.db',
  };
}

// ===== 逐类读取 =====
type ActivityContext = {
  db: SQLite.SQLiteDatabase;
  table: string;
  columns: Set<string>;
  /** 秒/毫秒换算系数。 */
  scale: number;
};

async function readHeartRates(context: ActivityContext): Promise<HeartRateRecord[]> {
  const { db, table } = context;
  const rows = await db.getAllAsync<{ TIMESTAMP: number; HEART_RATE: number }>(
    `SELECT TIMESTAMP, HEART_RATE FROM ${table} WHERE HEART_RATE > 0 ORDER BY TIMESTAMP ASC`,
  );
  const out: HeartRateRecord[] = [];
  for (const row of rows) {
    const heartRate = positiveOrNull(row.HEART_RATE);
    const timestamp = toIso(row.TIMESTAMP);
    if (heartRate !== null && timestamp !== '') {
      out.push({ timestamp, heartRate: Math.round(heartRate) });
    }
  }
  return out;
}

async function readSpo2(context: ActivityContext): Promise<Spo2Record[]> {
  if (!context.columns.has('SPO')) return [];
  const rows = await context.db.getAllAsync<{ TIMESTAMP: number; SPO: number }>(
    `SELECT TIMESTAMP, SPO FROM ${context.table} WHERE SPO > 0 AND SPO <= 100 ORDER BY TIMESTAMP ASC`,
  );
  const out: Spo2Record[] = [];
  for (const row of rows) {
    const spo2 = positiveOrNull(row.SPO);
    const timestamp = toIso(row.TIMESTAMP);
    if (spo2 !== null && timestamp !== '') {
      out.push({ timestamp, spo2: Math.round(spo2) });
    }
  }
  return out;
}

async function readRestingHeartRates(
  context: ActivityContext,
): Promise<RestingHrRecord[]> {
  if (!context.columns.has('RESTING_HEART_RATE')) return [];
  const hour = 3600 * context.scale;
  const rows = await context.db.getAllAsync<{ BUCKET: number; RHR: number }>(
    `SELECT (TIMESTAMP / ${hour}) * ${hour} AS BUCKET, MIN(RESTING_HEART_RATE) AS RHR ` +
      `FROM ${context.table} WHERE RESTING_HEART_RATE > 0 GROUP BY BUCKET ORDER BY BUCKET ASC`,
  );
  const out: RestingHrRecord[] = [];
  for (const row of rows) {
    const restingHeartRate = positiveOrNull(row.RHR);
    const timestamp = toIso(row.BUCKET);
    if (restingHeartRate !== null && timestamp !== '') {
      out.push({ timestamp, restingHeartRate: Math.round(restingHeartRate) });
    }
  }
  return out;
}

type BucketRow = {
  BUCKET: number;
  STEPS: number | null;
  DISTANCE: number | null;
  CALORIES: number | null;
};

/** 步数/距离/卡路里按桶累加，累加在 SQL 里完成。 */
async function readBuckets(context: ActivityContext): Promise<{
  steps: StepRecord[];
  distance: DistanceRecord[];
  calories: CaloriesRecord[];
}> {
  const { db, table, columns, scale } = context;
  const bucket = BUCKET_MINUTES * 60 * scale;
  const sum = (column: string) =>
    columns.has(column)
      ? `SUM(CASE WHEN ${column} >= 0 THEN ${column} ELSE 0 END) AS ${column}`
      : `0 AS ${column}`;

  const rows = await db.getAllAsync<BucketRow>(
    `SELECT (TIMESTAMP / ${bucket}) * ${bucket} AS BUCKET, ` +
      `${sum('STEPS')}, ${sum('DISTANCE')}, ${sum('CALORIES')} ` +
      `FROM ${table} GROUP BY BUCKET ORDER BY BUCKET ASC`,
  );

  const steps: StepRecord[] = [];
  const distance: DistanceRecord[] = [];
  const calories: CaloriesRecord[] = [];
  for (const row of rows) {
    const timestamp = toIso(row.BUCKET);
    if (timestamp === '') continue;
    const stepCount = numberOrNull(row.STEPS);
    if (stepCount !== null) steps.push({ timestamp, steps: stepCount });
    const meters = numberOrNull(row.DISTANCE);
    if (meters !== null) distance.push({ timestamp, distance: meters });
    const smallCalories = numberOrNull(row.CALORIES);
    if (smallCalories !== null) {
      calories.push({ timestamp, calories: round(smallCalories / 1000) });
    }
  }
  return { steps, distance, calories };
}

/** 单日汇总。日界按本机时区对齐，口径是全量数据。 */
async function readDaily(context: ActivityContext): Promise<DailySummary[]> {
  const { db, table, columns, scale } = context;
  const day = 86400 * scale;
  const zoneOffset = -new Date().getTimezoneOffset() * 60 * scale;
  const guard = (column: string) => (columns.has(column) ? column : '0');
  const aggregate = (column: string, fn: 'MIN' | 'MAX' | 'AVG') =>
    `${fn}(CASE WHEN ${guard(column)} > 0 THEN ${column} END) AS ${fn}_${column}`;
  const total = (column: string) =>
    columns.has(column)
      ? `SUM(CASE WHEN ${column} >= 0 THEN ${column} ELSE 0 END) AS SUM_${column}`
      : `0 AS SUM_${column}`;

  const rows = await db.getAllAsync<Record<string, number | null>>(
    `SELECT ((TIMESTAMP + ${zoneOffset}) / ${day}) * ${day} - ${zoneOffset} AS DAY, ` +
      `${total('STEPS')}, ${total('DISTANCE')}, ${total('CALORIES')}, ` +
      `${aggregate('HEART_RATE', 'MIN')}, ${aggregate('HEART_RATE', 'MAX')}, ${aggregate('HEART_RATE', 'AVG')}, ` +
      `${aggregate('SPO', 'MIN')}, ${aggregate('SPO', 'AVG')}, ` +
      `${aggregate('RESTING_HEART_RATE', 'MIN')} ` +
      `FROM ${table} GROUP BY DAY ORDER BY DAY ASC`,
  );

  const out: DailySummary[] = [];
  for (const row of rows) {
    const dayStart = toIso(numberOrNull(row.DAY) ?? 0);
    if (dayStart === '') continue;
    out.push({
      dayStart,
      steps: numberOrNull(row.SUM_STEPS) ?? 0,
      distanceMeters: numberOrNull(row.SUM_DISTANCE) ?? 0,
      caloriesKcal: round((numberOrNull(row.SUM_CALORIES) ?? 0) / 1000),
      heartRateMin: positiveOrNull(row.MIN_HEART_RATE),
      heartRateMax: positiveOrNull(row.MAX_HEART_RATE),
      heartRateAvg: positiveOrNull(row.AVG_HEART_RATE),
      spo2Min: positiveOrNull(row.MIN_SPO),
      spo2Avg: positiveOrNull(row.AVG_SPO),
      restingHeartRateMin: positiveOrNull(row.MIN_RESTING_HEART_RATE),
    });
  }
  return out;
}

type SleepStageRow = { TIMESTAMP: number; STAGE: number };
type SleepStatsRow = Record<string, number | string | null>;

/**
 * 把"每分钟一个阶段点"合并成连续阶段区间，再按间隔切成一次睡眠，
 * 并用同一夜的统计行补充睡眠评分、深睡时长和最低血氧。
 */
export function buildSleepSessions(
  stageRows: SleepStageRow[],
  statsRows: SleepStatsRow[],
  scale: number,
): SleepSession[] {
  const points: { seconds: number; stage: SleepStageName }[] = [];
  for (const row of stageRows) {
    const rawStage = numberOrNull(row.STAGE);
    const rawSeconds = numberOrNull(row.TIMESTAMP);
    if (rawStage === null || rawSeconds === null || rawSeconds <= 0) continue;
    const stage = HUAWEI_SLEEP_STAGE_MAP[rawStage];
    if (stage === undefined) continue;
    points.push({ seconds: Math.floor(rawSeconds / scale), stage });
  }
  points.sort((left, right) => left.seconds - right.seconds);
  if (points.length === 0) return [];

  const groups: typeof points[] = [];
  let current: typeof points = [];
  for (const point of points) {
    const previous = current[current.length - 1];
    if (previous !== undefined && point.seconds - previous.seconds > SLEEP_SESSION_GAP_SECONDS) {
      groups.push(current);
      current = [];
    }
    current.push(point);
  }
  if (current.length > 0) groups.push(current);

  const sessions: SleepSession[] = [];
  for (const group of groups) {
    const stages: SleepStageSlice[] = [];
    let runStart = group[0].seconds;
    let runStage = group[0].stage;
    for (let index = 1; index < group.length; index += 1) {
      const point = group[index];
      if (point.stage === runStage) continue;
      const startTime = toIso(runStart);
      const endTime = toIso(point.seconds);
      if (startTime !== '' && endTime > startTime) {
        stages.push({ startTime, endTime, stage: runStage });
      }
      runStart = point.seconds;
      runStage = point.stage;
    }
    const lastSeconds = group[group.length - 1].seconds;
    const lastStart = toIso(runStart);
    const lastEnd = toIso(lastSeconds + SLEEP_SAMPLE_SECONDS * scale);
    if (lastStart !== '' && lastEnd > lastStart) {
      stages.push({ startTime: lastStart, endTime: lastEnd, stage: runStage });
    }
    if (stages.length === 0) continue;

    const sessionStartSeconds = Math.floor(Date.parse(stages[0].startTime) / 1000);
    let matched: SleepStatsRow | null = null;
    let bestOffset = Number.POSITIVE_INFINITY;
    for (const stats of statsRows) {
      const rawBed = numberOrNull(stats.BED_TIME) ?? numberOrNull(stats.TIMESTAMP);
      if (rawBed === null || rawBed <= 0) continue;
      const offset = Math.abs(Math.floor(rawBed / scale) - sessionStartSeconds);
      if (offset < bestOffset && offset <= SLEEP_STATS_MATCH_WINDOW_SECONDS) {
        bestOffset = offset;
        matched = stats;
      }
    }

    sessions.push({
      startTime: stages[0].startTime,
      endTime: stages[stages.length - 1].endTime,
      stages,
      score: matched === null ? null : positiveOrNull(matched.SLEEP_SCORE),
      deepMinutes: matched === null ? null : positiveOrNull(matched.DEEP_PART),
      efficiency: matched === null ? null : positiveOrNull(matched.SLEEP_EFFICIENCY),
      minOxygenSaturation:
        matched === null ? null : positiveOrNull(matched.MIN_OXYGEN_SATURATION),
      minHeartRate: matched === null ? null : positiveOrNull(matched.MIN_HEART_RATE),
      maxHeartRate: matched === null ? null : positiveOrNull(matched.MAX_HEART_RATE),
    });
  }
  return sessions;
}

async function readSleep(
  db: SQLite.SQLiteDatabase,
  tables: string[],
  scale: number,
): Promise<SleepSession[]> {
  const stageTable = await findTable(db, tables, /SLEEP_STAGE_SAMPLE/i, 'STAGE');
  if (stageTable === null) return [];

  const stageRows = await db.getAllAsync<SleepStageRow>(
    `SELECT TIMESTAMP, STAGE FROM ${stageTable} ORDER BY TIMESTAMP ASC`,
  );

  const statsTable = await findTable(db, tables, /SLEEP_STATS_SAMPLE/i, 'SLEEP_SCORE');
  const statsRows =
    statsTable === null
      ? []
      : await db.getAllAsync<SleepStatsRow>(
          `SELECT * FROM ${statsTable} ORDER BY TIMESTAMP ASC`,
        );

  return buildSleepSessions(stageRows, statsRows, scale);
}

async function readStress(
  db: SQLite.SQLiteDatabase,
  tables: string[],
): Promise<StressRecord[]> {
  const table = await findTable(db, tables, /STRESS_SAMPLE/i, 'STRESS');
  if (table === null) return [];
  const rows = await db.getAllAsync<{ TIMESTAMP: number; STRESS: number }>(
    `SELECT TIMESTAMP, STRESS FROM ${table} WHERE STRESS >= 0 ORDER BY TIMESTAMP ASC`,
  );
  const out: StressRecord[] = [];
  for (const row of rows) {
    const stress = numberOrNull(row.STRESS);
    const timestamp = toIso(row.TIMESTAMP);
    if (stress !== null && timestamp !== '') {
      out.push({ timestamp, stress: Math.round(stress) });
    }
  }
  return out;
}

async function readDevice(
  db: SQLite.SQLiteDatabase,
  tables: string[],
): Promise<DeviceInfo | null> {
  if (!tables.some((name) => name.toUpperCase() === 'DEVICE')) return null;
  const row = await db.getFirstAsync<{
    MANUFACTURER: string | null;
    MODEL: string | null;
    NAME: string | null;
  }>(`SELECT MANUFACTURER, MODEL, NAME FROM DEVICE LIMIT 1`);
  if (row === null || row === undefined) return null;
  return {
    manufacturer: row.MANUFACTURER ?? null,
    model: row.MODEL ?? null,
    name: row.NAME ?? null,
  };
}

function summarise(bundle: Omit<HealthBundle, 'version' | 'fileName' | 'totals'>): HealthTotals {
  const heartRateValues = bundle.heartRates.map((item) => item.heartRate);
  const spo2Values = bundle.spo2.map((item) => item.spo2);
  const restingValues = bundle.restingHeartRates.map((item) => item.restingHeartRate);
  const stressValues = bundle.stress.map((item) => item.stress);

  const min = (values: number[]) => (values.length === 0 ? null : Math.min(...values));
  const avg = (values: number[]) =>
    values.length === 0
      ? null
      : Math.round(values.reduce((sum, value) => sum + value, 0) / values.length);

  const timestamps = [
    ...bundle.heartRates.map((item) => item.timestamp),
    ...bundle.spo2.map((item) => item.timestamp),
    ...bundle.steps.map((item) => item.timestamp),
  ].sort();

  // 清醒段不算睡眠时长
  const sleepMinutes = bundle.sleep.reduce(
    (total, session) =>
      total +
      session.stages.reduce((sum, slice) => {
        if (slice.stage === 'awake' || slice.stage === 'awake_in_bed') return sum;
        return sum + (Date.parse(slice.endTime) - Date.parse(slice.startTime)) / 60000;
      }, 0),
    0,
  );

  return {
    rangeStart: timestamps[0] ?? null,
    rangeEnd: timestamps[timestamps.length - 1] ?? null,
    dayCount: bundle.daily.length,
    heartRateCount: bundle.heartRates.length,
    heartRateMin: min(heartRateValues),
    heartRateMax: heartRateValues.length === 0 ? null : Math.max(...heartRateValues),
    heartRateAvg: avg(heartRateValues),
    spo2Count: bundle.spo2.length,
    spo2Min: min(spo2Values),
    spo2Avg: avg(spo2Values),
    restingHeartRateCount: bundle.restingHeartRates.length,
    restingHeartRateMin: min(restingValues),
    stepBucketCount: bundle.steps.length,
    totalSteps: Math.round(bundle.steps.reduce((total, item) => total + item.steps, 0)),
    totalDistanceMeters: Math.round(
      bundle.distance.reduce((total, item) => total + item.distance, 0),
    ),
    totalCaloriesKcal: Math.round(
      bundle.calories.reduce((total, item) => total + item.calories, 0),
    ),
    sleepSessionCount: bundle.sleep.length,
    sleepMinutes: Math.round(sleepMinutes),
    stressCount: bundle.stress.length,
    stressAvg: avg(stressValues),
  };
}

/** 打开 Gadgetbridge 导出的 .db，读出全部可用指标。 */
export async function parseGadgetbridgeDb(
  uri: string,
  fileName: string,
): Promise<HealthBundle> {
  // expo-sqlite 只能打开自己目录下的库，必须先复制过来
  const sqliteDir = `${FileSystem.documentDirectory}SQLite/`;
  await FileSystem.makeDirectoryAsync(sqliteDir, { intermediates: true });

  // 清掉上次导入留下的库，否则每导一次就多占一份空间
  const entries = await FileSystem.readDirectoryAsync(sqliteDir);
  for (const entry of entries) {
    if (entry.startsWith('gb_import_')) {
      await FileSystem.deleteAsync(`${sqliteDir}${entry}`, { idempotent: true });
    }
  }

  const destName = `gb_import_${Date.now()}.db`;
  await FileSystem.copyAsync({ from: uri, to: `${sqliteDir}${destName}` });

  const db = await SQLite.openDatabaseAsync(destName);
  try {
    const tables = await listTables(db);
    const table = await findActivityTable(db, tables);
    if (table === null) {
      throw new Error(
        `这个文件里没有活动数据表（${tables.length} 张表）。请确认选的是 Gadgetbridge「导出数据库」生成的文件。`,
      );
    }

    const columns = await tableColumns(db, table);
    const maxTimestamp = numberOrNull(
      (
        await db.getFirstAsync<{ MAX_TS: number | null }>(
          `SELECT MAX(TIMESTAMP) AS MAX_TS FROM ${table}`,
        )
      )?.MAX_TS,
    );
    const scale = maxTimestamp !== null && maxTimestamp > 1e12 ? 1000 : 1;
    const context: ActivityContext = { db, table, columns, scale };

    const [heartRates, spo2, restingHeartRates, buckets, daily, sleep, stress, device] =
      await Promise.all([
        readHeartRates(context),
        readSpo2(context),
        readRestingHeartRates(context),
        readBuckets(context),
        readDaily(context),
        readSleep(db, tables, scale),
        readStress(db, tables),
        readDevice(db, tables),
      ]);

    const partial = {
      bucketMinutes: BUCKET_MINUTES,
      heartRates,
      steps: buckets.steps,
      spo2,
      restingHeartRates,
      calories: buckets.calories,
      distance: buckets.distance,
      sleep,
      stress,
      daily,
      device,
    };

    return { version: 2, fileName, ...partial, totals: summarise(partial) };
  } finally {
    await db.closeAsync();
  }
}

/** 选文件并解析。 */
export async function pickAndParseHealth(): Promise<ParsedHealth> {
  const picked = await pickHealthFile();
  if (!picked.success || picked.uri === undefined) {
    return { success: false, message: picked.message };
  }
  const fileName = picked.fileName ?? 'gadgetbridge.db';

  try {
    const bundle = await parseGadgetbridgeDb(picked.uri, fileName);
    const { totals } = bundle;

    if (totals.heartRateCount + totals.stepBucketCount + totals.sleepSessionCount === 0) {
      return {
        success: false,
        message: `${fileName} 里没有解析到心率、步数或睡眠数据。`,
        fileName,
      };
    }

    const parts = [
      totals.heartRateCount > 0 ? `心率 ${totals.heartRateCount} 条` : null,
      totals.stepBucketCount > 0 ? `步数 ${totals.totalSteps} 步` : null,
      totals.spo2Count > 0 ? `血氧 ${totals.spo2Count} 条` : null,
      totals.restingHeartRateCount > 0
        ? `静息心率 ${totals.restingHeartRateCount} 条`
        : null,
      totals.sleepSessionCount > 0 ? `睡眠 ${totals.sleepSessionCount} 晚` : null,
      totals.stressCount > 0 ? `压力 ${totals.stressCount} 条` : null,
    ].filter((part): part is string => part !== null);

    return { success: true, message: parts.join('，'), fileName, bundle };
  } catch (error) {
    return {
      success: false,
      message: error instanceof Error ? error.message : '解析数据库失败',
      fileName,
    };
  }
}
