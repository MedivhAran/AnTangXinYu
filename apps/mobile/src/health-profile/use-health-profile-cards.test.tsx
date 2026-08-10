import { act, create } from 'react-test-renderer';

import { ApiError, type ApiClient } from '../api/client';
import type { HealthProfileCardDecision } from '../api/types';
import { useHealthProfileCards } from './use-health-profile-cards';

jest.mock('expo-crypto', () => ({
  randomUUID: () => '019b5555-5555-7555-8555-555555555555',
}));

const card = {
  id: '019b3333-3333-7333-8333-333333333333',
  kind: 'confirmation' as const,
  targetType: 'personal_profile',
  fieldName: 'weight_kg',
  operation: 'set',
  question: '更新体重吗？',
  proposedValue: { value: 62 },
  options: [
    { id: 'accept', label: '是，确认写入' },
    { id: 'reject', label: '暂不写入' },
  ],
  allowCustomInput: true,
  customInputPlaceholder: null,
  createdAt: '2026-07-14T10:00:00Z',
};

describe('useHealthProfileCards', () => {
  test('keeps a card until the server decision and subsequent refresh complete', async () => {
    let resolveDecision!: (value: HealthProfileCardDecision) => void;
    const decision = new Promise<HealthProfileCardDecision>((resolve) => {
      resolveDecision = resolve;
    });
    const api = {
      getHealthProfileCards: jest
        .fn()
        .mockResolvedValueOnce({ cards: [card] })
        .mockResolvedValueOnce({ cards: [] }),
      answerHealthProfileCard: jest.fn(() => decision),
    } as unknown as ApiClient;
    let current!: ReturnType<typeof useHealthProfileCards>;
    function Probe() {
      current = useHealthProfileCards(api);
      return null;
    }

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(<Probe />);
    });
    expect(current.cards).toEqual([card]);

    let pending!: Promise<void>;
    await act(async () => {
      pending = current.answer(card.id, { optionId: 'accept' });
      await Promise.resolve();
    });
    expect(current.cards).toEqual([card]);
    expect(current.actingCardId).toBe(card.id);

    await act(async () => {
      resolveDecision({ cardId: card.id, status: 'applied' });
      await pending;
    });
    expect(current.cards).toEqual([]);
    expect(current.actingCardId).toBeNull();
    expect(api.answerHealthProfileCard).toHaveBeenCalledTimes(1);
    act(() => tree!.unmount());
  });

  test('does not send a second decision during a rapid double tap', async () => {
    const api = {
      getHealthProfileCards: jest
        .fn()
        .mockResolvedValueOnce({ cards: [card] })
        .mockResolvedValue({ cards: [] }),
      answerHealthProfileCard: jest.fn(async () => ({
        cardId: card.id,
        status: 'applied' as const,
      })),
    } as unknown as ApiClient;
    let current!: ReturnType<typeof useHealthProfileCards>;
    function Probe() {
      current = useHealthProfileCards(api);
      return null;
    }

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(<Probe />);
    });
    await act(async () => {
      await Promise.all([
        current.answer(card.id, { optionId: 'accept' }),
        current.answer(card.id, { optionId: 'accept' }),
      ]);
    });
    expect(api.answerHealthProfileCard).toHaveBeenCalledTimes(1);
    act(() => tree!.unmount());
  });

  test('refreshes cards and reports a revision conflict returned by the server', async () => {
    const api = {
      getHealthProfileCards: jest
        .fn()
        .mockResolvedValueOnce({ cards: [card] })
        .mockResolvedValueOnce({ cards: [] }),
      answerHealthProfileCard: jest.fn(async () => ({
        cardId: card.id,
        status: 'conflicted' as const,
      })),
    } as unknown as ApiClient;
    let current!: ReturnType<typeof useHealthProfileCards>;
    function Probe() {
      current = useHealthProfileCards(api);
      return null;
    }

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(<Probe />);
    });
    await act(async () => {
      await current.answer(card.id, { optionId: 'accept' });
    });

    expect(api.getHealthProfileCards).toHaveBeenCalledTimes(2);
    expect(current.cards).toEqual([]);
    expect(current.error).toBe('健康档案已经发生变化，请查看最新的确认项。');
    act(() => tree!.unmount());
  });

  test('refreshes away a stale card when an older server returns detail.code 409', async () => {
    const api = {
      getHealthProfileCards: jest
        .fn()
        .mockResolvedValueOnce({ cards: [card] })
        .mockResolvedValueOnce({ cards: [] }),
      answerHealthProfileCard: jest.fn().mockRejectedValue(
        new ApiError('服务器返回 HTTP 409', 409, 'profile_card_changed'),
      ),
    } as unknown as ApiClient;
    let current!: ReturnType<typeof useHealthProfileCards>;
    function Probe() {
      current = useHealthProfileCards(api);
      return null;
    }

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(<Probe />);
    });
    await act(async () => {
      await current.answer(card.id, { optionId: 'accept' });
    });

    expect(api.getHealthProfileCards).toHaveBeenCalledTimes(2);
    expect(current.cards).toEqual([]);
    expect(current.error).toBe('这条确认项已经发生变化，已为你刷新。');
    expect(current.actingCardId).toBeNull();
    act(() => tree!.unmount());
  });
});
