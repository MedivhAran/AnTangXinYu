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

// ===== Gadgetbridge 导出目录（Android 外部存储路径） =====
const GADGETBRIDGE_EXPORT_DIR = `${FileSystem.ExternalDirectoryPath}/Gadgetbridge/files/export`;

// ===== 1. 检查 Gadgetbridge 导出目录是否存在 =====
export async function isGadgetbridgeExportAvailable(): Promise<boolean> {
  if (Platform.OS !== 'android') return false;
  try {
    const dirInfo = await FileSystem.getInfoAsync(GADGETBRIDGE_EXPORT_DIR);
    return dirInfo.exists;
  } catch {
    return false;
  }
}

// ===== 2. 获取所有 CSV 文件列表（按名称倒序） =====
export async function getCSVFiles(): Promise<FileSystem.FileInfo[]> {
  try {
    const dirInfo = await FileSystem.getInfoAsync(GADGETBRIDGE_EXPORT_DIR);
    if (!dirInfo.exists) {
      throw new Error('Gadgetbridge 导出目录不存在，请先在 Gadgetbridge 中导出数据');
    }

    const files = await FileSystem.readDirectoryAsync(GADGETBRIDGE_EXPORT_DIR);
    const csvFiles: FileSystem.FileInfo[] = [];

    for (const fileName of files) {
      if (fileName.endsWith('.csv')) {
        const filePath = `${GADGETBRIDGE_EXPORT_DIR}/${fileName}`;
        const fileInfo = await FileSystem.getInfoAsync(filePath);
        if (fileInfo.exists) {
          csvFiles.push(fileInfo);
        }
      }
    }

    // 按文件名倒序（最新的在前，假设文件名包含时间）
    csvFiles.sort((a, b) => {
      return b.uri.localeCompare(a.uri);
    });

    return csvFiles;
  } catch (error) {
    console.error('获取 CSV 文件列表失败:', error);
    throw error;
  }
}

// ===== 3. 解析 CSV 内容 =====
export function parseCSVContent(content: string): {
  heartRates: HeartRateRecord[];
  steps: StepRecord[];
} {
  const lines = content.split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0);

  const heartRates: HeartRateRecord[] = [];
  const steps: StepRecord[] = [];

  if (lines.length === 0) {
    return { heartRates, steps };
  }

  // 检测并跳过表头
  let startIndex = 0;
  const firstLine = lines[0]?.toLowerCase() || '';
  const headerKeywords = ['timestamp', 'time', 'date', '心率', 'heart', '步数', 'step', 'activity'];
  if (headerKeywords.some(keyword => firstLine.includes(keyword))) {
    startIndex = 1;
  }

  for (let i = startIndex; i < lines.length; i++) {
    const parts = lines[i].split(',').map(s => s.trim());
    if (parts.length < 2) continue;

    const timestamp = parts[0];
    let heartRate = 0;
    let stepValue = 0;

    // 遍历列寻找心率和步数
    for (let j = 1; j < parts.length && j < 6; j++) {
      const val = parseInt(parts[j]);
      if (isNaN(val)) continue;

      // 心率范围通常在 30-220 之间
      if (val >= 30 && val <= 220 && heartRate === 0) {
        heartRate = val;
      }
      // 步数通常 > 0 且可能较大
      if (val > 0 && val < 100000 && stepValue === 0 && parts[j].length > 0) {
        // 避免把心率误认为步数
        if (val < 30 || val > 220) {
          stepValue = val;
        } else if (heartRate > 0 && val >= 30 && val <= 220) {
          // 已经识别为心率，不重复赋值
        } else {
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

  return { heartRates, steps };
}

// ===== 4. 读取并解析最新的 CSV 文件 =====
export async function readLatestCSV(): Promise<{
  heartRates: HeartRateRecord[];
  steps: StepRecord[];
  fileName: string;
}> {
  try {
    const csvFiles = await getCSVFiles();

    if (csvFiles.length === 0) {
      throw new Error('没有找到 CSV 文件，请先在 Gadgetbridge 中导出数据');
    }

    const latestFile = csvFiles[0];
    const content = await FileSystem.readAsStringAsync(latestFile.uri);

    const result = parseCSVContent(content);
    const fileName = latestFile.uri.split('/').pop() || 'unknown.csv';

    return {
      ...result,
      fileName,
    };
  } catch (error) {
    console.error('读取 CSV 失败:', error);
    throw error;
  }
}

// ===== 5. 仅预览数据（不上传后端），用于调试 =====
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
      return {
        success: false,
        message: '未找到 Gadgetbridge 数据。\n请确保：\n1. 已安装 Gadgetbridge\n2. 已绑定手环并同步\n3. 已导出 CSV（菜单→数据管理→导出数据）'
      };
    }

    const { heartRates, steps, fileName } = await readLatestCSV();

    if (heartRates.length === 0 && steps.length === 0) {
      return {
        success: false,
        message: `文件 ${fileName} 中未找到心率或步数数据`,
        fileName,
      };
    }

    // 计算统计信息
    const avgHeartRate = heartRates.length > 0
      ? Math.round(heartRates.map(h => h.heartRate).reduce((a, b) => a + b, 0) / heartRates.length)
      : 0;
    const totalSteps = steps.map(s => s.steps).reduce((a, b) => a + b, 0);

    return {
      success: true,
      message: `✅ 文件: ${fileName}\n心率: ${heartRates.length} 条 (平均 ${avgHeartRate} bpm)\n步数: ${steps.length} 条 (总计 ${totalSteps} 步)`,
      heartRates,
      steps,
      fileName,
    };
  } catch (error: any) {
    return {
      success: false,
      message: error?.message || '预览失败，请检查 CSV 文件格式'
    };
  }
}

// ===== 6. 一键同步数据到后端 =====
export async function syncGadgetbridgeData(
  backendUrl: string,
  userId: string,
  token: string
): Promise<SyncResult> {
  try {
    // 1. 先预览数据
    const preview = await previewGadgetbridgeData();
    if (!preview.success) {
      return { success: false, message: preview.message };
    }

    const { heartRates, steps, fileName } = preview;
    if (!heartRates || heartRates.length === 0) {
      return { success: false, message: '没有心率数据可同步' };
    }

    // 2. 计算统计信息
    const totalSteps = steps?.map(s => s.steps).reduce((a, b) => a + b, 0) || 0;
    const avgHeartRate = heartRates.length > 0
      ? Math.round(heartRates.map(h => h.heartRate).reduce((a, b) => a + b, 0) / heartRates.length)
      : 0;
    const minHeartRate = heartRates.length > 0
      ? Math.min(...heartRates.map(h => h.heartRate))
      : 0;
    const maxHeartRate = heartRates.length > 0
      ? Math.max(...heartRates.map(h => h.heartRate))
      : 0;

    // 3. 组装请求体（限制数据量防止过大）
    const MAX_RECORDS = 500;
    const payload = {
      userId,
      source: 'gadgetbridge',
      syncedAt: new Date().toISOString(),
      fileName,
      totalSteps,
      avgHeartRate,
      minHeartRate,
      maxHeartRate,
      heartRateCount: heartRates.length,
      stepsCount: steps?.length || 0,
      heartRates: heartRates.slice(0, MAX_RECORDS),
      steps: steps?.slice(0, MAX_RECORDS) || [],
    };

    // 4. 调用后端 API
    const response = await fetch(`${backendUrl}/api/v1/health/sync`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const errorText = await response.text();
      return {
        success: false,
        message: `上传失败 (${response.status}): ${errorText}`,
        heartRates,
        steps,
      };
    }

    const data = await response.json();
    return {
      success: true,
      message: `✅ 同步成功！\n心率 ${heartRates.length} 条，步数 ${steps?.length || 0} 条\n平均心率 ${avgHeartRate} bpm，总计 ${totalSteps} 步`,
      data,
      heartRates,
      steps,
    };
  } catch (error: any) {
    console.error('Gadgetbridge 同步失败:', error);
    return {
      success: false,
      message: error?.message || '同步失败，请检查网络连接',
    };
  }
}

// ===== 7. 获取 Gadgetbridge 导出目录路径（用于调试） =====
export function getGadgetbridgeExportDir(): string {
  return GADGETBRIDGE_EXPORT_DIR;
}