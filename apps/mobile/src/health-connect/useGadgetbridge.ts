import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, AppState } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

import { API_URL } from '../config';
import { errorMessage } from '../api/client';
import {
  previewGadgetbridgeData,
  syncGadgetbridgeData,
} from '../services/GadgetbridgeService';

// ===== 状态类型（与原有 HealthConnectState 保持一致） =====
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

// ===== 从 AsyncStorage 获取用户信息 =====
async function getStoredAuth(): Promise<{ userId: string; token: string }> {
  try {
    const userId = await AsyncStorage.getItem('userId') || '';
    const token = await AsyncStorage.getItem('token') || '';
    return { userId, token };
  } catch {
    return { userId: '', token: '' };
  }
}

// ===== 主 Hook =====
export function useGadgetbridge(
  _userId?: string,
  _importer?: any,
  _gateway?: any,
  _tokenStore?: any,
) {
  const [state, setState] = useState(initialState);
  const mounted = useRef(true);
  const syncController = useRef<AbortController | null>(null);

  // 刷新状态：检查本地是否有 CSV 数据
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

  // 执行同步
  const runSync = useCallback(
    async (_requestPermissions: boolean): Promise<void> => {
      if (syncController.current) {
        console.log('同步已在进行中');
        return;
      }

      const controller = new AbortController();
      syncController.current = controller;

      const { userId, token } = await getStoredAuth();

      if (!userId || !token) {
        if (mounted.current) {
          setState((prev) => ({
            ...prev,
            syncing: false,
            error: '请先登录',
          }));
        }
        syncController.current = null;
        return;
      }

      if (mounted.current) {
        setState((prev) => ({
          ...prev,
          syncing: true,
          progress: '正在同步手环数据...',
          error: null,
        }));
      }

      try {
        // ✅ 使用项目配置的 API_URL，而不是硬编码
        const result = await syncGadgetbridgeData(API_URL, userId, token);

        if (controller.signal.aborted) {
          console.log('同步被取消');
          return;
        }

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
              connected: false,
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
        if (syncController.current === controller) {
          syncController.current = null;
        }
      }
    },
    [],
  );

  // 对外暴露的方法
  const connect = useCallback(() => runSync(true), [runSync]);
  const sync = useCallback(() => runSync(false), [runSync]);

  const stop = useCallback(() => {
    if (syncController.current) {
      syncController.current.abort(new Error('手环同步已取消'));
      syncController.current = null;
    }
    if (mounted.current) {
      setState((prev) => ({
        ...prev,
        syncing: false,
        progress: null,
      }));
    }
  }, []);

  // 后台同步（保留真实开关）
  const enableBackground = useCallback(async (): Promise<void> => {
    try {
      await AsyncStorage.setItem('backgroundSyncEnabled', 'true');
      if (mounted.current) {
        setState((prev) => ({
          ...prev,
          backgroundStatus: 'enabled',
          backgroundError: null,
        }));
      }
      Alert.alert('提示', '后台同步已开启');
    } catch (e) {
      Alert.alert('错误', '开启后台同步失败');
    }
  }, []);

  const disableBackground = useCallback(async (): Promise<void> => {
    try {
      await AsyncStorage.removeItem('backgroundSyncEnabled');
      if (mounted.current) {
        setState((prev) => ({
          ...prev,
          backgroundStatus: 'disabled',
          backgroundError: null,
        }));
      }
      Alert.alert('提示', '后台同步已关闭');
    } catch (e) {
      Alert.alert('错误', '关闭后台同步失败');
    }
  }, []);

  // 初始化
  useEffect(() => {
    mounted.current = true;
    refreshStatus();

    AsyncStorage.getItem('backgroundSyncEnabled').then((enabled) => {
      if (mounted.current) {
        setState((prev) => ({
          ...prev,
          backgroundStatus: enabled === 'true' ? 'enabled' : 'disabled',
        }));
      }
    });

    const subscription = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active') {
        refreshStatus();
        AsyncStorage.getItem('backgroundSyncEnabled').then((enabled) => {
          if (enabled === 'true') {
            runSync(false);
          }
        });
      }
    });

    return () => {
      mounted.current = false;
      syncController.current?.abort(new Error('组件已卸载'));
      subscription.remove();
    };
  }, [refreshStatus, runSync]);

  return {
    ...state,
    connect,
    sync,
    stop,
    enableBackground,
    disableBackground,
  };
}