import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';

import { errorMessage } from '../api/client';
import {
  disableBackgroundHealthSync,
  enableBackgroundHealthSync,
  getBackgroundHealthSyncInfo,
  type BackgroundHealthSyncStatus,
} from './background-health-sync';
import type { WearableImporter } from './sync-health-connect';
import type {
  HealthConnectGateway,
  SupportedHealthConnectRecordType,
} from './health-connect-types';
import {
  secureHealthConnectTokenStore,
  type HealthConnectStore,
} from './health-connect-store';
import {
  nativeHealthConnect,
  prepareHealthConnect,
} from './native-health-connect';
import { runHealthConnectSync } from './sync-health-connect';

const recordLabels: Record<SupportedHealthConnectRecordType, string> = {
  Steps: '步数',
  ExerciseSession: '锻炼',
  Distance: '距离',
  ElevationGained: '爬升高度',
  Weight: '体重',
  RespiratoryRate: '呼吸频率',
  RestingHeartRate: '静息心率',
  HeartRate: '心率',
  SleepSession: '睡眠',
  OxygenSaturation: '血氧',
};

export type HealthConnectState = {
  checking: boolean;
  connected: boolean;
  syncing: boolean;
  progress: string | null;
  lastSyncedAt: string | null;
  error: string | null;
  backgroundStatus: BackgroundHealthSyncStatus | 'checking';
  backgroundError: string | null;
  backgroundEnabling: boolean;
  backgroundDisabling: boolean;
};

const initialState: HealthConnectState = {
  checking: true,
  connected: false,
  syncing: false,
  progress: null,
  lastSyncedAt: null,
  error: null,
  backgroundStatus: 'checking',
  backgroundError: null,
  backgroundEnabling: false,
  backgroundDisabling: false,
};

export function useHealthConnect(
  userId: string,
  importer: WearableImporter,
  gateway: HealthConnectGateway = nativeHealthConnect,
  tokenStore: HealthConnectStore = secureHealthConnectTokenStore,
) {
  const [state, setState] = useState(initialState);
  const mounted = useRef(true);
  const syncPromise = useRef<Promise<void> | null>(null);
  const syncController = useRef<AbortController | null>(null);
  const connected = useRef(false);

  const refreshBackgroundStatus = useCallback(async (): Promise<void> => {
    try {
      const info = await getBackgroundHealthSyncInfo(userId);
      if (mounted.current) {
        setState((current) => ({
          ...current,
          backgroundStatus: info.status,
          backgroundError: info.error,
        }));
      }
    } catch (error) {
      if (mounted.current) {
        setState((current) => ({
          ...current,
          backgroundStatus: 'checking',
          backgroundError: errorMessage(error),
        }));
      }
    }
  }, [userId]);

  const runSync = useCallback(
    (requestPermissions: boolean): Promise<void> => {
      if (syncPromise.current !== null) return syncPromise.current;
      const controller = new AbortController();
      syncController.current = controller;

      const request = (async () => {
        let prepared = false;
        if (mounted.current) {
          setState((current) => ({
            ...current,
            checking: false,
            syncing: true,
            progress: requestPermissions ? '正在连接 Health Connect' : '正在同步手环数据',
            error: null,
          }));
        }
        try {
          await prepareHealthConnect(gateway, requestPermissions);
          prepared = true;
          if (requestPermissions) {
            await tokenStore.setActiveUserId(userId);
          }
          connected.current = true;
          await runHealthConnectSync(
            userId,
            gateway,
            tokenStore,
            importer,
            ({ recordType, completedTypes, totalTypes }) => {
              if (mounted.current) {
                setState((current) => ({
                  ...current,
                  progress: `正在同步${recordLabels[recordType]}（${completedTypes + 1}/${totalTypes}）`,
                }));
              }
            },
            new Date(),
            controller.signal,
          );
          await tokenStore.markInitialSyncComplete(userId);
          if (mounted.current) {
            setState((current) => ({
              ...current,
              checking: false,
              connected: true,
              syncing: false,
              progress: null,
              lastSyncedAt: new Date().toISOString(),
              error: null,
            }));
          }
          void refreshBackgroundStatus();
        } catch (error) {
          if (!prepared) connected.current = false;
          if (mounted.current) {
            setState((current) => ({
              ...current,
              checking: false,
              connected: connected.current,
              syncing: false,
              progress: null,
              error: errorMessage(error),
            }));
          }
          throw error;
        } finally {
          if (syncController.current === controller) syncController.current = null;
        }
      })().finally(() => {
        if (syncPromise.current === request) syncPromise.current = null;
      });

      syncPromise.current = request;
      return request;
    },
    [gateway, importer, refreshBackgroundStatus, tokenStore, userId],
  );

  const restoreAndSync = useCallback(async (): Promise<void> => {
    try {
      const activeUserId = await tokenStore.getActiveUserId();
      if (activeUserId !== userId) {
        connected.current = false;
        if (mounted.current) {
          setState((current) => ({
            ...initialState,
            checking: false,
            backgroundStatus: current.backgroundStatus,
            backgroundError: current.backgroundError,
          }));
        }
        return;
      }
      connected.current = true;
      await runSync(false);
    } catch (error) {
      if (mounted.current) {
        setState((current) => ({
          ...initialState,
          checking: false,
          connected: connected.current,
          error: errorMessage(error),
          backgroundStatus: current.backgroundStatus,
          backgroundError: current.backgroundError,
        }));
      }
    }
  }, [runSync, tokenStore, userId]);

  useEffect(() => {
    mounted.current = true;
    void refreshBackgroundStatus();
    void restoreAndSync();
    const subscription = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active' && connected.current) {
        void runSync(false).catch(() => {
          // runSync has already placed the error in the visible connection state.
        });
      }
    });
    return () => {
      mounted.current = false;
      syncController.current?.abort(new Error('手环同步已取消'));
      subscription.remove();
    };
  }, [refreshBackgroundStatus, restoreAndSync, runSync]);

  const enableBackground = useCallback(async (): Promise<void> => {
    setState((current) => ({
      ...current,
      backgroundEnabling: true,
      backgroundError: null,
    }));
    try {
      await enableBackgroundHealthSync(userId);
      await refreshBackgroundStatus();
    } catch (error) {
      if (mounted.current) {
        setState((current) => ({
          ...current,
          backgroundError: errorMessage(error),
        }));
      }
      throw error;
    } finally {
      if (mounted.current) {
        setState((current) => ({ ...current, backgroundEnabling: false }));
      }
    }
  }, [refreshBackgroundStatus, userId]);

  const disableBackground = useCallback(async (): Promise<void> => {
    setState((current) => ({
      ...current,
      backgroundDisabling: true,
      backgroundError: null,
    }));
    try {
      await disableBackgroundHealthSync(userId);
      await refreshBackgroundStatus();
    } catch (error) {
      if (mounted.current) {
        setState((current) => ({
          ...current,
          backgroundError: errorMessage(error),
        }));
      }
      throw error;
    } finally {
      if (mounted.current) {
        setState((current) => ({ ...current, backgroundDisabling: false }));
      }
    }
  }, [refreshBackgroundStatus, userId]);

  return {
    ...state,
    connect: () => runSync(true),
    sync: () => runSync(false),
    stop: () => syncController.current?.abort(new Error('手环同步已取消')),
    enableBackground,
    disableBackground,
  };
}
