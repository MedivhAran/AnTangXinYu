import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert } from 'react-native';

import { errorMessage } from '../api/client';
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

export function useGadgetbridge(_userId?: string) {
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
      const result = await pickAndReadCSV();
      if (!mounted.current) return;

      if (result.success) {
        setState((prev) => ({
          ...prev,
          connected: true,
          syncing: false,
          progress: null,
          lastSyncedAt: new Date().toISOString(),
          error: null,
        }));
        Alert.alert('✅ 手环数据读取成功', result.message);
      } else {
        setState((prev) => ({
          ...prev,
          syncing: false,
          progress: null,
          error: result.message,
        }));
        Alert.alert('读取失败', result.message);
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
  }, []);

  const connect = useCallback(() => runSync(), [runSync]);
  const sync = useCallback(() => runSync(), [runSync]);

  const stop = useCallback(() => {
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