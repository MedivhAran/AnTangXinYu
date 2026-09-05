import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, AppState } from 'react-native';

import { errorMessage } from '../api/client';
import {
  previewGadgetbridgeData,
  syncGadgetbridgeData,
} from '../services/GadgetbridgeService';
import { useAuth } from '../hooks/useAuth'; // 假设项目中有这个 hook，如果没有请自行替换为获取 userId 和 token 的方式

// 保持与原来相同的状态类型，方便上层组件使用
export type HealthConnectState = {
  checking: boolean;
  connected: boolean;
  syncing: boolean;
  progress: string | null;
  lastSyncedAt: string | null;
  error: string | null;
  // 以下后台相关字段保留但不再使用，统一设为 'unsupported' 避免上层报错
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

// 注意：保留原参数列表以兼容已有调用，但实际不再使用 _importer、_gateway、_tokenStore
export function useHealthConnect(
  userId: string,
  _importer?: any,
  _gateway?: any,
  _tokenStore?: any,
) {
  const [state, setState] = useState(initialState);
  const mounted = useRef(true);
  const syncController = useRef<AbortController | null>(null);

  // 从 Auth 上下文获取用户信息和 token（需要你的项目中有对应的 hook）
  // 如果项目中没有 useAuth，请替换为其他获取 token 的方式（比如从 AsyncStorage 读取）
  const { user, token } = useAuth();

  // 刷新连接状态：检查 Gadgetbridge 是否已有导出的数据
  const refreshStatus = useCallback(async () => {
    try {
      const result = await previewGadgetbridgeData();
      if (mounted.current) {
        setState((prev) => ({
          ...prev,
          checking: false,
          connected: result.success && (result.heartRates?.length ?? 0) > 0,
          error: result.success ? null : result.message,
          // 如果成功，展示第一条数据的时间作为进度提示
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

  // 执行同步（requestPermissions 参数保留但 Gadgetbridge 不需要权限请求）
  const runSync = useCallback(
    async (requestPermissions: boolean): Promise<void> => {
      // 如果已有同步任务进行中，则不重复执行
      if (syncController.current) {
        return;
      }

      const controller = new AbortController();
      syncController.current = controller;

      if (mounted.current) {
        setState((prev) => ({
          ...prev,
          checking: false,
          syncing: true,
          progress: requestPermissions ? '正在检查手环数据' : '正在同步手环数据',
          error: null,
        }));
      }

      try {
        // 确保用户已登录
        if (!user || !token) {
          throw new Error('用户未登录，请先登录');
        }

        // 后端地址，建议从环境变量读取，这里暂时硬编码占位，请替换为实际地址
        const backendUrl = 'http://your-backend-url/api/v1/health/sync';

        const result = await syncGadgetbridgeData(
          backendUrl,
          user.id || userId,
          token,
        );

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
            // 可选提示
            Alert.alert('✅ 同步成功', result.message);
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
            connected: false,
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
    [user, token, userId],
  );

  // 对外暴露的连接方法（对应 "连接手环" 按钮）
  const connect = useCallback(() => runSync(true), [runSync]);

  // 对外暴露的同步方法（对应 "立即同步" 按钮）
  const sync = useCallback(() => runSync(false), [runSync]);

  // 停止同步
  const stop = useCallback(() => {
    syncController.current?.abort(new Error('手环同步已取消'));
    syncController.current = null;
    if (mounted.current) {
      setState((prev) => ({
        ...prev,
        syncing: false,
        progress: null,
      }));
    }
  }, []);

  // 后台同步功能暂不支持，保留空实现以免上层报错
  const enableBackground = useCallback(async (): Promise<void> => {
    Alert.alert('提示', '后台同步暂不支持，请在前台手动同步');
    return Promise.resolve();
  }, []);

  const disableBackground = useCallback(async (): Promise<void> => {
    Alert.alert('提示', '后台同步暂不支持');
    return Promise.resolve();
  }, []);

  // 初始化时检查状态，并在 App 回到前台时刷新
  useEffect(() => {
    mounted.current = true;
    refreshStatus();

    const subscription = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active') {
        refreshStatus(); // 回到前台刷新状态，但不自动同步
      }
    });

    return () => {
      mounted.current = false;
      syncController.current?.abort(new Error('组件已卸载'));
      subscription.remove();
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