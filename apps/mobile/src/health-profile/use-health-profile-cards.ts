import { randomUUID } from 'expo-crypto';
import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiClient, ApiError, errorMessage } from '../api/client';
import type { HealthProfileCard, HealthProfileCardAnswer } from '../api/types';

type HealthProfileCardsState = {
  cards: HealthProfileCard[];
  loading: boolean;
  actingCardId: string | null;
  error: string | null;
};

/**
 * 待确认项始终以服务器为准。按钮操作期间不会先从界面删除卡片，
 * 只有服务器完成决定并再次返回列表后，界面才会更新。
 */
export function useHealthProfileCards(api: ApiClient) {
  const mounted = useRef(true);
  const refreshPromise = useRef<Promise<void> | null>(null);
  const lastRefreshSucceeded = useRef(false);
  const actingCardId = useRef<string | null>(null);
  const [state, setState] = useState<HealthProfileCardsState>({
    cards: [],
    loading: true,
    actingCardId: null,
    error: null,
  });

  const refresh = useCallback((): Promise<void> => {
    if (refreshPromise.current !== null) return refreshPromise.current;

    const request = (async () => {
      lastRefreshSucceeded.current = false;
      if (mounted.current) {
        setState((current) => ({ ...current, loading: true, error: null }));
      }
      try {
        const result = await api.getHealthProfileCards();
        lastRefreshSucceeded.current = true;
        if (mounted.current) {
          setState((current) => ({
            ...current,
            cards: result.cards,
            loading: false,
            error: null,
          }));
        }
      } catch (error) {
        if (mounted.current) {
          setState((current) => ({
            ...current,
            loading: false,
            error: errorMessage(error),
          }));
        }
      }
    })().finally(() => {
      refreshPromise.current = null;
    });

    refreshPromise.current = request;
    return request;
  }, [api]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
    };
  }, [refresh]);

  const answer = useCallback(
    async (cardId: string, answerValue: HealthProfileCardAnswer): Promise<void> => {
      // The ref closes the tiny gap before React applies the state update, so
      // two fast taps cannot send two decisions for the same visible card.
      if (actingCardId.current !== null) return;
      actingCardId.current = cardId;
      setState((current) => ({
        ...current,
        actingCardId: cardId,
        error: null,
      }));

      try {
        const result = await api.answerHealthProfileCard(
          cardId,
          randomUUID(),
          answerValue,
        );
        // A chat-terminal refresh may have started before the decision reached
        // the server. Wait for it, then start a definitely newer request.
        if (refreshPromise.current !== null) await refreshPromise.current;
        if (result.status === 'conflicted' && mounted.current) {
          setState((current) => ({
            ...current,
            cards: current.cards.filter((card) => card.id !== cardId),
          }));
        }
        await refresh();
        if (
          mounted.current &&
          result.status === 'conflicted' &&
          lastRefreshSucceeded.current
        ) {
          setState((current) => ({
            ...current,
            error: '健康档案已经发生变化，请查看最新的确认项。',
          }));
        }
      } catch (error) {
        if (
          error instanceof ApiError &&
          error.status === 409 &&
          error.code === 'profile_card_changed'
        ) {
          if (refreshPromise.current !== null) await refreshPromise.current;
          if (mounted.current) {
            setState((current) => ({
              ...current,
              cards: current.cards.filter((card) => card.id !== cardId),
            }));
          }
          await refresh();
          if (mounted.current && lastRefreshSucceeded.current) {
            setState((current) => ({
              ...current,
              error: '这条确认项已经发生变化，已为你刷新。',
            }));
          }
          return;
        }
        if (mounted.current) {
          setState((current) => ({ ...current, error: errorMessage(error) }));
        }
      } finally {
        actingCardId.current = null;
        if (mounted.current) {
          setState((current) => ({ ...current, actingCardId: null }));
        }
      }
    },
    [api, refresh],
  );

  return { ...state, refresh, answer };
}
