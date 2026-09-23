import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert } from 'react-native';

import { API_URL, IS_DEMO_MODE } from '../config';
import { errorMessage } from '../api/client';
import { secureTokenStore } from '../auth/token-store';
import {
  pickAndParseHealth,
  type HealthBundle,
} from '../services/GadgetbridgeService';

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
  lastBundle: HealthBundle | null;
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
  lastBundle: null,
};

async function uploadToBackend(
  userId: string,
  bundle: HealthBundle,
  fileName: string,
  signal: AbortSignal,
): Promise<{ success: boolean; message: string; status?: number }> {
  const tokens = await secureTokenStore.load();
  if (!tokens) {
    return { success: false, message: '登录已过期，请重新登录' };
  }

  const endpoint = `${API_URL.replace(/\/+$/, '').trim()}/api/v1/health-profile/wearable-imports`;

  const { heartRates, steps } = bundle;
  const avgHeartRate =
    heartRates.length > 0
      ? Math.round(
        heartRates.reduce((a, b) => a + b.heartRate, 0) / heartRates.length,
      )
      : 0;
  const totalSteps = steps.reduce((a, b) => a + b.steps, 0);

  const payload: Record<string, unknown> = {
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

  // 有血氧/静息心率才带 extras，避免后端 422 浪费一次往返
  if (bundle.spo2.length > 0 || bundle.restingHeartRates.length > 0) {
    payload.extras = {
      spo2: bundle.spo2,
      restingHeartRates: bundle.restingHeartRates,
      calories: bundle.calories,
      distance: bundle.distance,
    };
  }

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${tokens.accessToken}`,
      },
      body: JSON.stringify(payload),
      signal,
    });

    if (!response.ok) {
      const text = await response.text();
      return {
        success: false,
        status: response.status,
        message: `上传失败(${response.status}): ${text.slice(0, 200)}`,
      };
    }

    return { success: true, message: '已上传后端' };
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      return { success: false, message: '已取消' };
    }
    return { success: false, message: e?.message || '网络错误' };
  }
}

export function useGadgetbridge(userId?: string) {
  const [state, setState] = useState(initialState);
  const mounted = useRef(true);
  const abortRef = useRef<AbortController | null>(null);

  const runSync = useCallback(async (): Promise<void> => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    if (mounted.current) {
      setState((prev) => ({
        ...prev,
        syncing: true,
        progress: '请选择 Gadgetbridge 导出的 .db 或 .csv 文件...',
        error: null,
      }));
    }

    try {
      const result = await pickAndParseHealth();
      if (!mounted.current) return;

      if (!result.success || !result.bundle) {
        setState((prev) => ({
          ...prev,
          syncing: false,
          progress: null,
          error: result.message,
        }));
        Alert.alert('读取失败', result.message);
        return;
      }

      // 先落地 bundle，看板立即可用
      setState((prev) => ({
        ...prev,
        lastBundle: result.bundle!,
        progress: `解析完成：${result.message}，准备上传...`,
      }));

      if (!userId) {
        setState((prev) => ({
          ...prev,
          syncing: false,
          progress: null,
          connected: true,
          lastSyncedAt: new Date().toISOString(),
          error: null,
        }));
        Alert.alert('提示', '未登录，数据已解析并展示在看板，未上传后端');
        return;
      }

      if (IS_DEMO_MODE) {
        setState((prev) => ({
          ...prev,
          syncing: false,
          progress: null,
          connected: true,
          lastSyncedAt: new Date().toISOString(),
        }));
        Alert.alert('演示模式', '已解析数据，跳过上传');
        return;
      }

      setState((prev) => ({ ...prev, progress: '正在上传到服务器...' }));
      const upload = await uploadToBackend(
        userId,
        result.bundle,
        result.fileName ?? 'unknown.db',
        controller.signal,
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
          `${result.message}\n\n${upload.message}\n\n现在可以在聊天里问我："分析一下我的手环数据"`,
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
    } finally {
      abortRef.current = null;
    }
  }, [userId]);

  const connect = useCallback(() => runSync(), [runSync]);
  const sync = useCallback(() => runSync(), [runSync]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (mounted.current) {
      setState((prev) => ({ ...prev, syncing: false, progress: null }));
    }
  }, []);

  const enableBackground = useCallback(async () => {
    Alert.alert('提示', '暂不支持后台同步');
  }, []);
  const disableBackground = useCallback(async () => {
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
    // 兼容 ChatScreen 横幅里已用到的字段
    heartRateCount: state.lastBundle?.heartRates.length ?? 0,
    connect,
    sync,
    stop,
    enableBackground,
    disableBackground,
  };
}