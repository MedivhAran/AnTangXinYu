import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert } from 'react-native';

import { API_URL } from '../config';
import { errorMessage } from '../api/client';
import { secureTokenStore } from '../auth/token-store';
import { pickAndReadCSV } from '../services/GadgetbridgeService';

export type HealthConnectState = {
  checking: boolean;
  connected: boolean;
  syncing: boolean;
  progress: string | null;
  lastSyncedAt: string | null;
  error: string | null;
  backgroundStatus: 'checking' | 'enabled' | 'disabled' | 'unsupported';
  backgroundError: string | null;
  backgroundEnabling: boolean;
  backgroundDisabling: boolean;
};

const initialState: HealthConnectState = {
  checking: false,
  connected: false,
  syncing: false,
  progress: null,
  lastSyncedAt: null,
  error: null,
  backgroundStatus: 'unsupported',
  backgroundError: null,
  backgroundEnabling: false,
  backgroundDisabling: false,
};

// ============ 上传函数 ============
async function uploadToBackend(

  userId: string,
  heartRates: Array<{ timestamp: string; heartRate: number }>,
  steps: Array<{ timestamp: string; steps: number }>,
  fileName: string,
): Promise<{ success: boolean; message: string; status?: number }> {
  const tokens = await secureTokenStore.load();
  if (!tokens) {
    return { success: false, message: '登录已过期，请重新登录' };
  }

  const endpoint = `${API_URL.replace(/\/+$/, '')} /api/v1 / health - profile / wearable - imports`;

  const avgHeartRate =
    heartRates.length > 0
      ? Math.round(
        heartRates.reduce((a, b) => a + b.heartRate, 0) / heartRates.length,
      )
      : 0;
  const totalSteps = steps.reduce((a, b) => a + b.steps, 0);

  const payload = {
    client_sync_id: `gb-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
    userId,
    source: 'gadgetbridge',
    fileName,
    totalSteps,
    avgHeartRate,
    heartRateCount: heartRates.length,
    stepsCount: steps.length,
    heartRates,
    steps,
    syncedAt: new Date().toISOString(),
  };

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${tokens.accessToken} `,
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const text = await response.text();
      return {
        success: false,
        status: response.status,
        message: `上传失败(${response.status}): ${text.slice(0, 200)} `,
      };
    }

    return {
      success: true,
      message: `心率 ${heartRates.length} 条、步数 ${steps.length} 条已上传`,
    };
  } catch (e: any) {
    return { success: false, message: e?.message || '网络错误' };
  }
}
// ===================================

export function useGadgetbridge(userId?: string) {
  const [state, setState] = useState(initialState);
  const mounted = useRef(true);

  const runSync = useCallback(async (): Promise<void> => {
    if (mounted.current) {
      setState((prev) => ({
        ...prev,
        syncing: true,
        progress: '请选择 CSV 文件...',
        error: null,
      }));
    }

    try {
      // 1. 让用户选 CSV 并解析
      const result = await pickAndReadCSV();
      if (!mounted.current) return;

      if (!result.success) {
        setState((prev) => ({
          ...prev,
          syncing: false,
          progress: null,
          error: result.message,
        }));
        Alert.alert('读取失败', result.message);
        return;
      }

      // 2. 检查登录状态
      if (!userId) {
        setState((prev) => ({
          ...prev,
          syncing: false,
          progress: null,
          error: '请先登录',
        }));
        Alert.alert('提示', '请先登录再上传数据');
        return;
      }

      if (!result.heartRates || !result.steps) {
        setState((prev) => ({
          ...prev,
          syncing: false,
          progress: null,
          error: 'CSV 解析结果为空',
        }));
        return;
      }

      // 3. 上传到后端
      if (mounted.current) {
        setState((prev) => ({
          ...prev,
          progress: '正在上传到服务器...',
        }));
      }

      const upload = await uploadToBackend(
        userId,
        result.heartRates,
        result.steps,
        result.fileName ?? 'unknown.csv',
      );

      if (!mounted.current) return;

      if (upload.success) {
        setState((prev) => ({
          ...prev,
          connected: true,
          syncing: false,
          progress: null,
          lastSyncedAt: new Date().toISOString(),
          error: null,
        }));
        Alert.alert(
          '✅ 手环数据已上传',
          `${result.message} \n\n${upload.message} \n\n现在可以在聊天里问我："分析一下我的手环数据"`,
        );
      } else {
        setState((prev) => ({
          ...prev,
          syncing: false,
          progress: null,
          error: upload.message,
        }));
        Alert.alert('上传失败', upload.message);
      }
    } catch (error) {
      if (mounted.current) {
        setState((prev) => ({
          ...prev,
          syncing: false,
          progress: null,
          error: errorMessage(error),
        }));
      }
    }
  }, [userId]);

  const connect = useCallback(() => runSync(), [runSync]);
  const sync = useCallback(() => runSync(), [runSync]);

  const stop = useCallback(() => {
    if (mounted.current) {
      setState((prev) => ({ ...prev, syncing: false, progress: null }));
    }
  }, []);

  const enableBackground = useCallback(async (): Promise<void> => {
    Alert.alert('提示', '暂不支持后台同步');
  }, []);

  const disableBackground = useCallback(async (): Promise<void> => {
    Alert.alert('提示', '暂不支持后台同步');
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  return {
    ...state,
    connect,
    sync,
    stop,
    enableBackground,
    disableBackground,
  };
}