import { Platform } from 'react-native';
import * as FileSystem from 'expo-file-system';

// ===== 类型定义 =====
export type HeartRateRecord = {
  timestamp: string;
  heartRate: number;
};

export type StepRecord = {
  timestamp: string;
  steps: number;
};

export type SyncResult = {
  success: boolean;
  message: string;
  data?: any;
  heartRates?: HeartRateRecord[];
  steps?: StepRecord[];
  fileName?: string;
};

// ===== 获取 Gadgetbridge 导出目录 =====
function getExportDir(): string {
  const base = FileSystem.ExternalDirectoryPath || '/storage/emulated/0';
  return `${base}/Gadgetbridge/files/export`;
}

// ===== 1. 检查目录是否存在 =====
export async function isGadgetbridgeExportAvailable(): Promise<boolean> {
  if (Platform.OS !== 'android') return false;
  try {
    const dir = getExportDir();
    const dirInfo = await FileSystem.getInfoAsync(dir);
    return dirInfo.exists;
  } catch {
    return false;
  }
}

// ===== 2. 获取所有 CSV 文件列表 =====
export async function getCSVFiles(): Promise<FileSystem.FileInfo[]> {
  const dir = getExportDir();
  const dirInfo = await FileSystem.getInfoAsync(dir);
  if (!dirInfo.exists) {
    throw new Error('Gadgetbridge 导出目录不存在，请先在 Gadgetbridge 中导出数据');
  }

  const files = await FileSystem.readDirectoryAsync(dir);
  const csvFiles: FileSystem.FileInfo[] = [];

  for (const fileName of files) {
    if (fileName.toLowerCase().endsWith('.csv')) {
      const filePath = `${dir}/${fileName}`;
      const fileInfo = await FileSystem.getInfoAsync(filePath);
      if (fileInfo.exists) {
        csvFiles.push(fileInfo);
      }
    }
  }

  csvFiles.sort((a, b) => b.uri.localeCompare(a.uri));
  return csvFiles;
}

// ===== 3. 解析 CSV（按表头匹配，不靠猜） =====
export function parseCSVContent(content: string): {
  heartRates: HeartRateRecord[];
  steps: StepRecord[];
  headers: string[];
} {
  const lines = content.split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0);

  const heartRates: HeartRateRecord[] = [];
  const steps: StepRecord[] = [];

  if (lines.length < 2) {
    return { heartRates, steps, headers: [] };
  }

  const headerLine = lines[0];
  const headers = headerLine.split(',').map(h => h.trim().toLowerCase());

  let heartRateCol = -1;
  let stepsCol = -1;
  let timestampCol = 0;

  for (let i = 0; i < headers.length; i++) {
    const h = headers[i];
    if (h.includes('heart') || h.includes('心率')) heartRateCol = i;
    if (h.includes('step') || h.includes('步数')) stepsCol = i;
    if (h.includes('timestamp') || h.includes('time') || h.includes('日期') || h.includes('时间')) {
      timestampCol = i;
    }
  }

  for (let i = 1; i < lines.length; i++) {
    const parts = lines[i].split(',').map(s => s.trim());
    if (parts.length < 2) continue;

    const timestamp = parts[timestampCol] || '';

    let heartRate = 0;
    let stepValue = 0;

    if (heartRateCol >= 0 && heartRateCol < parts.length) {
      heartRate = parseInt(parts[heartRateCol]) || 0;
    }
    if (stepsCol >= 0 && stepsCol < parts.length) {
      stepValue = parseInt(parts[stepsCol]) || 0;
    }

    // 备选：如果没找到明确的列，尝试按范围推断（仅当匹配失败时）
    if (heartRateCol === -1 && stepsCol === -1) {
      for (let j = 0; j < Math.min(parts.length, 6); j++) {
        const val = parseInt(parts[j]);
        if (isNaN(val)) continue;
        if (val >= 30 && val <= 220 && heartRate === 0) {
          heartRate = val;
        } else if (val > 0 && val < 100000 && stepValue === 0) {
          stepValue = val;
        }
      }
    }

    if (heartRate > 0) {
      heartRates.push({ timestamp, heartRate });
    }
    if (stepValue > 0) {
      steps.push({ timestamp, steps: stepValue });
    }
  }

  return { heartRates, steps, headers };
}

// ===== 4. 读取最新的 CSV 文件 =====
export async function readLatestCSV(): Promise<{
  heartRates: HeartRateRecord[];
  steps: StepRecord[];
  fileName: string;
  headers: string[];
}> {
  const csvFiles = await getCSVFiles();
  if (csvFiles.length === 0) {
    throw new Error('未找到 CSV 文件');
  }

  const latestFile = csvFiles[0];
  const content = await FileSystem.readAsStringAsync(latestFile.uri);
  const { heartRates, steps, headers } = parseCSVContent(content);
  const fileName = latestFile.uri.split('/').pop() || 'unknown.csv';

  return { heartRates, steps, fileName, headers };
}

// ===== 5. 预览数据 =====
export async function previewGadgetbridgeData(): Promise<{
  success: boolean;
  message: string;
  heartRates?: HeartRateRecord[];
  steps?: StepRecord[];
  fileName?: string;
}> {
  try {
    const available = await isGadgetbridgeExportAvailable();
    if (!available) {
      return { success: false, message: '未找到 Gadgetbridge 数据，请先在 Gadgetbridge 中导出 CSV' };
    }

    const { heartRates, steps, fileName } = await readLatestCSV();

    if (heartRates.length === 0 && steps.length === 0) {
      return { success: false, message: `文件 ${fileName} 中未找到心率或步数数据` };
    }

    const avgHeartRate = heartRates.length > 0
      ? Math.round(heartRates.reduce((a, b) => a + b.heartRate, 0) / heartRates.length)
      : 0;
    const totalSteps = steps.reduce((a, b) => a + b.steps, 0);

    return {
      success: true,
      message: `✅ 心率: ${heartRates.length} 条 (平均 ${avgHeartRate} bpm)\n步数: ${steps.length} 条 (总计 ${totalSteps} 步)`,
      heartRates,
      steps,
      fileName,
    };
  } catch (error: any) {
    return { success: false, message: error?.message || '预览失败' };
  }
}

// ===== 6. 分批上传到后端 =====
export async function syncGadgetbridgeData(
  backendUrl: string,
  userId: string,
  token: string
): Promise<SyncResult> {
  const baseUrl = backendUrl.replace(/\/+$/, '');
  const endpoint = `${baseUrl}/api/v1/health-profile/wearable-imports`;

  try {
    const preview = await previewGadgetbridgeData();
    if (!preview.success || !preview.heartRates) {
      return { success: false, message: preview.message };
    }

    const { heartRates, steps, fileName } = preview;
    const totalSteps = steps?.reduce((a, b) => a + b.steps, 0) || 0;
    const avgHeartRate = heartRates.length > 0
      ? Math.round(heartRates.reduce((a, b) => a + b.heartRate, 0) / heartRates.length)
      : 0;

    // 分批上传（每批 500 条）
    const BATCH_SIZE = 500;
    const batches: HeartRateRecord[][] = [];
    for (let i = 0; i < heartRates.length; i += BATCH_SIZE) {
      batches.push(heartRates.slice(i, i + BATCH_SIZE));
    }

    let uploadedCount = 0;
    let failedBatches = 0;

    for (let i = 0; i < batches.length; i++) {
      const batch = batches[i];
      const payload = {
        userId,
        source: 'gadgetbridge',
        fileName,
        batch: i + 1,
        totalBatches: batches.length,
        totalSteps,
        avgHeartRate,
        heartRateCount: heartRates.length,
        heartRates: batch,
        steps: steps || [],
        syncedAt: new Date().toISOString(),
      };

      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(payload),
        });

        if (!response.ok) {
          const errorText = await response.text();
          console.error(`第 ${i + 1} 批上传失败:`, errorText);
          failedBatches++;
        } else {
          uploadedCount += batch.length;
        }
      } catch (err) {
        console.error(`第 ${i + 1} 批请求失败:`, err);
        failedBatches++;
      }
    }

    if (failedBatches > 0 && uploadedCount === 0) {
      return {
        success: false,
        message: `上传失败：全部 ${batches.length} 批均失败`,
        heartRates,
        steps,
      };
    }

    return {
      success: true,
      message: `✅ 同步完成！心率 ${uploadedCount} 条，步数 ${steps?.length || 0} 条${failedBatches > 0 ? ` (${failedBatches} 批失败)` : ''}`,
      heartRates,
      steps,
      data: { total: uploadedCount, failedBatches },
    };
  } catch (error: any) {
    return {
      success: false,
      message: error?.message || '同步失败',
    };
  }
}