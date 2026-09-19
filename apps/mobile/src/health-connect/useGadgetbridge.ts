import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, AppState } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

import { API_URL, IS_DEMO_MODE } from '../config';
import { errorMessage } from '../api/client';
import {
  previewGadgetbridgeData,
  syncGadgetbridgeData,
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

async function getStoredAuth(): Promise<{ userId: string; token: string }> {
  try {
    const userId = (await AsyncStorage.getItem('userId')) || '';
    const token = (await AsyncStorage.getItem('token')) || '';
    return { userId, token };
  } catch {
    return { userId: '', token: '' };
  }
}

export function useGadgetbridge(_userId?: string) {
  const [state, setState] = useState(initialState);
  const mounted = useRef(true);
  const syncController = useRef<AbortController | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      const result = await previewGadgetbridgeData();
      if (mounted.current) {
        setState((prev) => ({
          ...prev,
          checking: false,
          connected: result.success && (result.heartRates?.length ?? 0) > 0,
          error: result.success ? null : result.message,
          progress: result.success
            ? `找到 ${result.heartRates?.length || 0} 条心率记录`
            : null,
        }));
      }
    } catch (error) {
      if (mounted.current) {
        setState((prev) => ({
          ...prev,
          checking: false,
          connected: false,
          error: errorMessage(error),
        }));
      }
    }
  }, []);

  const runSync = useCallback(async (_requestPermissions: boolean): Promise<void> => {
    if (syncController.current) return;
    const controller = new AbortController();
    syncController.current = controller;

    if (mounted.current) {
      setState((prev) => ({
        ...prev,
        syncing: true,
        progress: '正在读取手环数据...',
        error: null,
      }));
    }

    try {
      // ============ 演示模式：只读取 CSV，不上传 ============
      if (IS_DEMO_MODE) {
        const result = await previewGadgetbridgeData();
        if (controller.signal.aborted) return;

        if (!result.success) {
          if (mounted.current) {
            setState((prev) => ({
              ...prev,
              syncing: false,
              progress: null,
              error: result.message,
            }));
          }
          Alert.alert('读取失败', result.message);
          return;
        }

        const heartCount = result.heartRates?.length ?? 0;
        const stepCount = result.steps?.length ?? 0;

        if (mounted.current) {
          setState((prev) => ({
            ...prev,
            connected: true,
            syncing: false,
            progress: null,
            lastSyncedAt: new Date().toISOString(),
            error: null,
          }));
        }
        Alert.alert(
          '✅ 手环数据读取成功',
          `心率: ${heartCount} 条\n步数: ${stepCount} 条\n\n（演示模式：数据未上传后端）`,
        );
        return;
      }

      // ============ 正常模式：需要登录 ============
      const { userId, token } = await getStoredAuth();
      if (!userId || !token) {
        if (mounted.current) {
          setState((prev) => ({
            ...prev,
            syncing: false,
            progress: null,
            error: '请先登录',
          }));
        }
        return;
      }

      const result = await syncGadgetbridgeData(API_URL, userId, token);
      if (controller.signal.aborted) return;

      if (mounted.current) {
        if (result.success) {
          setState((prev) => ({
            ...prev,
            connected: true,
            syncing: false,
            progress: null,
            lastSyncedAt: new Date().toISOString(),
            error: null,
          }));
        } else {
          setState((prev) => ({
            ...prev,
            syncing: false,
            progress: null,
            error: result.message,
          }));
        }
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
      if (syncController.current === controller) syncController.current = null;
    }
  }, []);

  const connect = useCallback(() => runSync(true), [runSync]);
  const sync = useCallback(() => runSync(false), [runSync]);

  const stop = useCallback(() => {
    syncController.current?.abort(new Error('已取消'));
    syncController.current = null;
    if (mounted.current) {
      setState((prev) => ({ ...prev, syncing: false, progress: null }));
    }
  }, []);

  const enableBackground = useCallback(async (): Promise<void> => {
    Alert.alert('提示', '演示模式下不支持后台同步');
  }, []);

  const disableBackground = useCallback(async (): Promise<void> => {
    Alert.alert('提示', '演示模式下不支持后台同步');
  }, []);

  useEffect(() => {
    mounted.current = true;
    refreshStatus();
    return () => {
      mounted.current = false;
      syncController.current?.abort();
    };
  }, [refreshStatus]);

  return {
    ...state,
    connect,
    sync,
    stop,
    enableBackground,
    disableBackground,
  };
}