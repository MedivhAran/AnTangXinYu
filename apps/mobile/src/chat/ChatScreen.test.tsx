import { act, create } from 'react-test-renderer';
import { FlatList, TextInput } from 'react-native';

import type { ApiClient } from '../api/client';
import { ChatScreen } from './ChatScreen';

const mockRefreshHealthProfileCards = jest.fn().mockResolvedValue(undefined);

jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: jest.requireActual('react-native').View,
}));

jest.mock('../health-profile/use-health-profile-cards', () => ({
  useHealthProfileCards: () => ({
    actingCardId: null,
    cards: [],
    error: null,
    loading: false,
    answer: jest.fn(),
    refresh: mockRefreshHealthProfileCards,
  }),
}));

jest.mock('../health-connect/use-health-connect', () => ({
  useHealthConnect: () => ({
    backgroundDisabling: false,
    backgroundEnabling: false,
    backgroundError: null,
    backgroundStatus: 'disabled',
    checking: false,
    connected: true,
    error: null,
    lastSyncedAt: null,
    progress: null,
    syncing: false,
    connect: jest.fn(),
    disableBackground: jest.fn(),
    enableBackground: jest.fn(),
    stop: jest.fn(),
    sync: jest.fn(),
  }),
}));

jest.mock('../notifications/use-notifications', () => ({
  useNotifications: () => ({
    permission: 'granted',
    requestingPermission: false,
    requestPermission: jest.fn(),
  }),
}));

jest.mock('../health-overview', () => ({
  HealthOverviewScreen: () => null,
}));

jest.mock('../health-profile/health-profile-cards', () => ({
  HealthProfileCards: () => null,
}));

jest.mock('../navigation/app-menu', () => ({
  AppMenu: () => null,
}));

jest.mock('../proactive-care/care-settings-modal', () => ({
  CareSettingsModal: () => null,
}));

describe('ChatScreen', () => {
  test('disables pull-to-refresh while a reply stream is active', async () => {
    let finishStream!: () => void;
    let emitStreamEvent!: (event: {
      type: 'message_failed';
      code: string;
      errorType: string;
    }) => void;
    const stream = new Promise<void>((resolve) => {
      finishStream = resolve;
    });
    const api = {
      getMessages: jest.fn().mockResolvedValue({
        messages: [],
        nextBefore: null,
      }),
      streamMessage: jest.fn(
        (
          _clientMessageId: string,
          _content: string,
          onEvent: typeof emitStreamEvent,
        ) => {
          emitStreamEvent = onEvent;
          return stream;
        },
      ),
    } as unknown as ApiClient;

    let tree: ReturnType<typeof create>;
    await act(async () => {
      tree = create(
        <ChatScreen
          api={api}
          onSignedOut={jest.fn()}
          user={{ id: 'user-1', username: 'demo' }}
        />,
      );
      await Promise.resolve();
    });

    expect(tree!.root.findByType(FlatList).props.onRefresh).toEqual(
      expect.any(Function),
    );

    act(() => {
      tree!.root.findByType(TextInput).props.onChangeText('我有点担心低血糖');
    });
    act(() => {
      tree!.root.findByProps({ accessibilityLabel: '发送消息' }).props.onPress();
    });

    expect(api.streamMessage).toHaveBeenCalledTimes(1);
    expect(tree!.root.findByType(FlatList).props.onRefresh).toBeUndefined();

    await act(async () => {
      emitStreamEvent({
        type: 'message_failed',
        code: 'agent_run_failed',
        errorType: 'TestFailure',
      });
      finishStream();
      await stream;
    });
    act(() => tree!.unmount());
  });
});
