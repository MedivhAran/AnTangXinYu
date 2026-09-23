import { Platform } from 'react-native';
import * as FileSystem from 'expo-file-system/legacy';
import * as DocumentPicker from 'expo-document-picker';
import * as SQLite from 'expo-sqlite';

// ===== 类型 =====
export type HeartRateRecord = { timestamp: string; heartRate: number };
export type StepRecord = { timestamp: string; steps: number };
export type Spo2Record = { timestamp: string; spo2: number };
export type RestingHrRecord = { timestamp: string; restingHeartRate: number };
export type CaloriesRecord = { timestamp: string; calories: number };
export type DistanceRecord = { timestamp: string; distance: number };

export type HealthBundle = {
  heartRates: HeartRateRecord[];
  steps: StepRecord[];
  spo2: Spo2Record[];
  restingHeartRates: RestingHrRecord[];
  calories: CaloriesRecord[];
  distance: DistanceRecord[];
};

export type SyncResult = {
  success: boolean;
  message: string;
  data?: any;
  heartRates?: HeartRateRecord[];
  steps?: StepRecord[];
  bundle?: HealthBundle;
  fileName?: string;
};

// ===== 工具：时间戳统一转 ISO =====
// Gadgetbridge 导出的 TIMESTAMP 是 Unix 秒；有的设备给毫秒，做个兼容
function toIso(ts: number | string): string {
  const n = typeof ts === 'string' ? parseInt(ts, 10) : ts;
  if (!n || isNaN(n)) return '';
  const ms = n > 1e12 ? n : n * 1000;
  return new Date(ms).toISOString();
}

// ===== 1. 选文件：优先 .db，也允许 .csv 兜底 =====
export async function pickHealthFile(): Promise<{
  success: boolean;
  message: string;
  kind?: 'db' | 'csv';
  uri?: string;
  fileName?: string;
}> {
  try {
    const result = await DocumentPicker.getDocumentAsync({
      type: '*/*',
      copyToCacheDirectory: true,
      multiple: false,
    });

    if (result.canceled || !result.assets?.length) {
      return { success: false, message: '未选择文件' };
    }

    const file = result.assets[0];
    const name = (file.name || 'unknown').toLowerCase();
    const kind: 'db' | 'csv' = name.endsWith('.db') ? 'db' : 'csv';

    return {
      success: true,
      message: '已选择',
      kind,
      uri: file.uri,
      fileName: file.name || 'unknown',
    };
  } catch (e: any) {
    return { success: false, message: e?.message || '选择文件失败' };
  }
}

// ===== 2. 读 .db（expo-sqlite）=====
export async function parseGadgetbridgeDb(uri: string): Promise<HealthBundle> {
  // 必须复制到 expo-sqlite 默认目录，openDatabaseAsync 不接受任意绝对路径
  const sqliteDir = `${FileSystem.documentDirectory}SQLite/`;
  await FileSystem.makeDirectoryAsync(sqliteDir, { intermediates: true });

  const destName = `gb_import_${Date.now()}.db`;
  const destUri = `${sqliteDir}${destName}`;

  // 清理同名（理论上不会重名，但保险）
  try {
    await FileSystem.deleteAsync(destUri, { idempotent: true });
  } catch { }

  await FileSystem.copyAsync({ from: uri, to: destUri });

  const db = await SQLite.openDatabaseAsync(destName);

  // 动态找表：优先包含 ACTIVITY 的表，否则找字段最像的
  const tables = await db.getAllAsync<{ name: string }>(
    `SELECT name FROM sqlite_master WHERE type='table'`
  );
  const tableNames = tables.map(t => t.name);
  if (tableNames.length === 0) {
    await db.closeAsync();
    throw new Error('数据库里没有表');
  }

  // 优先 HUAWEI_ACTIVITY_SAMPLE / MI_BAND_ACTIVITY_SAMPLE，其次任何含 ACTIVITY 的
  let tableName =
    tableNames.find(n => /HUAWEI_ACTIVITY_SAMPLE/i.test(n)) ||
    tableNames.find(n => /ACTIVITY_SAMPLE/i.test(n)) ||
    tableNames.find(n => /ACTIVITY/i.test(n));

  if (!tableName) {
    // 最后兜底：找一个同时有 TIMESTAMP 和 HEART_RATE 的表
    for (const n of tableNames) {
      const cols = await db.getAllAsync<{ name: string }>(`PRAGMA table_info(${n})`);
      const set = new Set(cols.map(c => c.name.toUpperCase()));
      if (set.has('TIMESTAMP') && (set.has('HEART_RATE') || set.has('STEPS'))) {
        tableName = n;
        break;
      }
    }
  }

  if (!tableName) {
    await db.closeAsync();
    throw new Error(`未找到活动数据表。现有表: ${tableNames.join(', ')}`);
  }

  const cols = await db.getAllAsync<{ name: string }>(`PRAGMA table_info(${tableName})`);
  const colSet = new Set(cols.map(c => c.name.toUpperCase()));

  const wanted = [
    'TIMESTAMP',
    'HEART_RATE',
    'STEPS',
    'SPO',
    'RESTING_HEART_RATE',
    'CALORIES',
    'DISTANCE',
  ].filter(c => colSet.has(c));

  if (!wanted.includes('TIMESTAMP')) {
    await db.closeAsync();
    throw new Error(`表 ${tableName} 没有 TIMESTAMP 字段`);
  }

  const rows = await db.getAllAsync<Record<string, number>>(
    `SELECT ${wanted.join(',')} FROM ${tableName} ORDER BY TIMESTAMP ASC`
  );

  await db.closeAsync();

  return aggregateRows(rows, wanted);
}

// ===== 3. 行聚合：同一秒多行取有效值；-1 视为无效 =====
function aggregateRows(
  rows: Record<string, number>[],
  wanted: string[]
): HealthBundle {
  const bundle: HealthBundle = {
    heartRates: [],
    steps: [],
    spo2: [],
    restingHeartRates: [],
    calories: [],
    distance: [],
  };

  // 按 TIMESTAMP 聚合（同一秒可能有两行：聚合行 + 活动行）
  const byTs = new Map<number, Record<string, number>>();

  for (const row of rows) {
    const ts = Number(row['TIMESTAMP']);
    if (!ts || ts <= 0) continue;

    const cur = byTs.get(ts) || {};
    for (const key of wanted) {
      const v = Number(row[key]);
      if (v === undefined || v === null || isNaN(v) || v < 0) continue;
      // 已有更大的有效值就保留；步数/卡路里等取"最大值"更贴近真实
      if (cur[key] === undefined || v > cur[key]) {
        cur[key] = v;
      }
    }
    byTs.set(ts, cur);
  }

  for (const [ts, v] of byTs.entries()) {
    const iso = toIso(ts);

    if (v['HEART_RATE'] !== undefined && v['HEART_RATE'] > 0) {
      bundle.heartRates.push({ timestamp: iso, heartRate: v['HEART_RATE'] });
    }
    if (v['STEPS'] !== undefined) {
      // 步数允许 0，保留（看板画图需要连续）
      bundle.steps.push({ timestamp: iso, steps: v['STEPS'] });
    }
    if (v['SPO'] !== undefined && v['SPO'] > 0) {
      bundle.spo2.push({ timestamp: iso, spo2: v['SPO'] });
    }
    if (v['RESTING_HEART_RATE'] !== undefined && v['RESTING_HEART_RATE'] > 0) {
      bundle.restingHeartRates.push({
        timestamp: iso,
        restingHeartRate: v['RESTING_HEART_RATE'],
      });
    }
    if (v['CALORIES'] !== undefined && v['CALORIES'] > 0) {
      bundle.calories.push({ timestamp: iso, calories: v['CALORIES'] });
    }
    if (v['DISTANCE'] !== undefined && v['DISTANCE'] > 0) {
      bundle.distance.push({ timestamp: iso, distance: v['DISTANCE'] });
    }
  }

  return bundle;
}

// ===== 4. CSV 兜底（保留你原来的路径，但改多指标 + ISO 时间）=====
export function parseCSVContent(content: string): HealthBundle & { headers: string[] } {
  const lines = content
    .split('\n')
    .map(l => l.trim())
    .filter(l => l.length > 0);

  const empty: HealthBundle & { headers: string[] } = {
    heartRates: [],
    steps: [],
    spo2: [],
    restingHeartRates: [],
    calories: [],
    distance: [],
    headers: [],
  };

  if (lines.length < 2) return empty;

  const headers = lines[0].split(',').map(h => h.trim().toUpperCase());
  const idx = (names: string[]) =>
    headers.findIndex(h => names.some(n => h.includes(n)));

  const col = {
    ts: idx(['TIMESTAMP', 'TIME', '日期', '时间']),
    hr: idx(['HEART_RATE', 'HEART', '心率']),
    steps: idx(['STEPS', 'STEP', '步数']),
    spo: idx(['SPO']),
    restingHr: idx(['RESTING_HEART_RATE']),
    cal: idx(['CALORIES']),
    dist: idx(['DISTANCE']),
  };

  if (col.ts < 0) return empty;

  const bundle: HealthBundle = {
    heartRates: [],
    steps: [],
    spo2: [],
    restingHeartRates: [],
    calories: [],
    distance: [],
  };

  for (let i = 1; i < lines.length; i++) {
    const parts = lines[i].split(',').map(s => s.trim());
    if (parts.length < 2) continue;

    const ts = parseInt(parts[col.ts], 10);
    if (!ts || isNaN(ts)) continue;
    const iso = toIso(ts);

    const num = (c: number) => (c >= 0 ? parseInt(parts[c], 10) : NaN);

    const hr = num(col.hr);
    if (!isNaN(hr) && hr > 0) bundle.heartRates.push({ timestamp: iso, heartRate: hr });

    const st = num(col.steps);
    if (!isNaN(st) && st >= 0) bundle.steps.push({ timestamp: iso, steps: st });

    const spo = num(col.spo);
    if (!isNaN(spo) && spo > 0) bundle.spo2.push({ timestamp: iso, spo2: spo });

    const rhr = num(col.restingHr);
    if (!isNaN(rhr) && rhr > 0)
      bundle.restingHeartRates.push({ timestamp: iso, restingHeartRate: rhr });

    const cal = num(col.cal);
    if (!isNaN(cal) && cal > 0) bundle.calories.push({ timestamp: iso, calories: cal });

    const dist = num(col.dist);
    if (!isNaN(dist) && dist > 0) bundle.distance.push({ timestamp: iso, distance: dist });
  }

  return { ...bundle, headers };
}

// ===== 5. 统一入口：选文件 → 解析 → 返回 bundle =====
export async function pickAndParseHealth(): Promise<{
  success: boolean;
  message: string;
  kind?: 'db' | 'csv';
  fileName?: string;
  bundle?: HealthBundle;
}> {
  const picked = await pickHealthFile();
  if (!picked.success || !picked.uri) {
    return { success: false, message: picked.message };
  }

  try {
    let bundle: HealthBundle;
    if (picked.kind === 'db') {
      bundle = await parseGadgetbridgeDb(picked.uri);
    } else {
      const content = await FileSystem.readAsStringAsync(picked.uri);
      const parsed = parseCSVContent(content);
      bundle = parsed;
    }

    const total =
      bundle.heartRates.length +
      bundle.steps.length +
      bundle.spo2.length +
      bundle.restingHeartRates.length;

    if (total === 0) {
      return {
        success: false,
        message: `文件 ${picked.fileName} 中未解析到任何健康数据`,
        kind: picked.kind,
        fileName: picked.fileName,
      };
    }

    const summary = [
      `心率 ${bundle.heartRates.length} 条`,
      `步数 ${bundle.steps.length} 条`,
      bundle.spo2.length ? `血氧 ${bundle.spo2.length} 条` : null,
      bundle.restingHeartRates.length ? `静息心率 ${bundle.restingHeartRates.length} 条` : null,
    ]
      .filter(Boolean)
      .join('，');

    return {
      success: true,
      message: summary,
      kind: picked.kind,
      fileName: picked.fileName,
      bundle,
    };
  } catch (e: any) {
    return { success: false, message: e?.message || '解析失败' };
  }
}

// ===== 6. 兼容旧调用（保留 CSV 专用入口，避免别处引用直接挂）=====
export async function pickAndReadCSV() {
  const r = await pickAndParseHealth();
  return {
    success: r.success,
    message: r.message,
    heartRates: r.bundle?.heartRates,
    steps: r.bundle?.steps,
    fileName: r.fileName,
  };
}

// ===== 7. 保留你原来的外部目录扫描（不删，兼容旧 UI 调用）=====
function getExportDir(): string {
  const base = (FileSystem as any).ExternalDirectoryPath || '/storage/emulated/0';
  return `${base}/Gadgetbridge/files/export`;
}

export async function isGadgetbridgeExportAvailable(): Promise<boolean> {
  if (Platform.OS !== 'android') return false;
  try {
    const info = await FileSystem.getInfoAsync(getExportDir());
    return info.exists;
  } catch {
    return false;
  }
}