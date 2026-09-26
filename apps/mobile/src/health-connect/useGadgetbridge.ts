import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert } from 'react-native';
import { randomUUID } from 'expo-crypto';

import { errorMessage } from '../api/client';
import {
  pickAndParseHealth,
  type HealthBundle,
} from '../services/GadgetbridgeService';
import { buildImportBatches } from './normalize-gadgetbridge';
import type { WearableImporter } from './sync-health-connect';

// =====================================================================
// Gadgetbridge 手环导入（手动选文件）
//
// 与 Health Connect 那条路的区别：Gadgetbridge 的数据在手机上的一个 .db 文件里，
// 没有后台推送也没有 Changes Token，所以**只能由用户手动选文件导入**。
// 因此这里没有"后台同步"和"前台自动同步"这两个概念，也不应该有对应开关。
//
// 上传一律走 ApiClient.importWearableRecords（即下面的 importer 参数），
// 它已经实现了后端要求的强类型请求体和分批逻辑。不要再自己写 fetch。
// =====================================================================

const RECORD_TYPE_LABEL: Record<string, string> = {
  heart_rate: '心率',
  steps: '步数',
  oxygen_saturation: '血氧',
  resting_heart_rate: '静息心率',
  distance: '距离',
  sleep: '睡眠',
};

export type GadgetbridgeState = {
  /** 正在解析选中的数据库（4 万行需要一点时间）。 */
  checking: boolean;
  /** 已经成功解析过至少一次数据。 */
  connected: boolean;
  /** 正在上传到后端。 */
  syncing: boolean;
  progress: string | null;
  lastSyncedAt: string | null;
  error: string | null;
  /** 解析结果，供健康看板直接渲染。 */
  lastBundle: HealthBundle | null;
};

const initialState: GadgetbridgeState = {
  checking: false,
  connected: false,
  syncing: false,
  progress: null,
  lastSyncedAt: null,
  error: null,
  lastBundle: null,
};

function abortError(): Error {
  const error = new Error('手环同步已取消');
  error.name = 'AbortError';
  return error;
}

/** 把解析出来的数据按后端契约分批上传，返回一句可展示的结果摘要。 */
async function uploadBundle(
  importer: WearableImporter,
  bundle: HealthBundle,
  signal: AbortSignal,
  onProgress: (message: string) => void,
): Promise<string> {
  const batches = buildImportBatches(bundle, randomUUID);
  // 同一批次的网络重试必须复用 client_sync_id，所以先按类型聚合出总数
  const totalByType = batches.reduce<Record<string, number>>((counts, batch) => {
    counts[batch.recordType] = (counts[batch.recordType] ?? 0) + batch.records.length;
    return counts;
  }, {});

  let created = 0;
  let updated = 0;
  let unchanged = 0;

  for (let index = 0; index < batches.length; index += 1) {
    if (signal.aborted) throw abortError();
    const batch = batches[index];
    const label = RECORD_TYPE_LABEL[batch.recordType] ?? batch.recordType;
    onProgress(`正在上传 ${label}（第 ${index + 1}/${batches.length} 批）…`);

    const result = (await importer.importWearableRecords(
      batch.clientSyncId,
      batch.recordType,
      // 手动导入的是一份有限窗口的文件，不是完整健康上下文
      false,
      batch.records,
      [],
      signal,
    )) as {
      recordsCreated?: number;
      recordsUpdated?: number;
      recordsUnchanged?: number;
    };

    created += result?.recordsCreated ?? 0;
    updated += result?.recordsUpdated ?? 0;
    unchanged += result?.recordsUnchanged ?? 0;
  }

  if (signal.aborted) throw abortError();

  const detail = Object.entries(totalByType)
    .map(([recordType, count]) => `${RECORD_TYPE_LABEL[recordType] ?? recordType} ${count}`)
    .join('、');
  return `已上传 ${detail}（新增 ${created}、更新 ${updated}、未变化 ${unchanged}）`;
}

export function useGadgetbridge(importer: WearableImporter) {
  const [state, setState] = useState<GadgetbridgeState>(initialState);
  const mounted = useRef(true);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const runSync = useCallback(async (): Promise<void> => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState((previous) => ({
      ...previous,
      checking: true,
      syncing: false,
      progress: '正在解析手环数据库…',
      error: null,
    }));

    try {
      const result = await pickAndParseHealth();
      if (!mounted.current) return;

      if (!result.success || result.bundle === undefined) {
        setState((previous) => ({
          ...previous,
          checking: false,
          progress: null,
          error: result.message,
        }));
        Alert.alert('读取手环数据失败', result.message);
        return;
      }

      // 先把 bundle 落地，看板立刻可用；上传失败也不影响本地查看
      setState((previous) => ({
        ...previous,
        checking: false,
        connected: true,
        lastBundle: result.bundle!,
        progress: `已解析：${result.message}`,
      }));

      setState((previous) => ({ ...previous, syncing: true }));
      const summary = await uploadBundle(
        importer,
        result.bundle,
        controller.signal,
        (message) => {
          if (mounted.current) {
            setState((previous) => ({ ...previous, progress: message }));
          }
        },
      );
      if (!mounted.current) return;

      setState((previous) => ({
        ...previous,
        syncing: false,
        progress: null,
        lastSyncedAt: new Date().toISOString(),
        error: null,
      }));
      Alert.alert(
        '手环数据已上传',
        `${result.message}\n\n${summary}\n\n现在可以在聊天里问：“帮我看看最近的手环数据”`,
      );
    } catch (error) {
      if (!mounted.current) return;
      if (error instanceof Error && error.name === 'AbortError') {
        setState((previous) => ({ ...previous, checking: false, syncing: false, progress: null }));
        return;
      }
      const message = errorMessage(error);
      setState((previous) => ({
        ...previous,
        checking: false,
        syncing: false,
        progress: null,
        error: message,
      }));
      Alert.alert('上传手环数据失败', message);
    } finally {
      abortRef.current = null;
    }
  }, [importer]);

  const connect = useCallback(() => {
    void runSync();
  }, [runSync]);

  const sync = useCallback(() => {
    void runSync();
  }, [runSync]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState((previous) => ({ ...previous, checking: false, syncing: false, progress: null }));
  }, []);

  return {
    ...state,
    heartRateCount: state.lastBundle?.heartRates.length ?? 0,
    connect,
    sync,
    stop,
  };
}
