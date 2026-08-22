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
  test('renders a photo attachment returned by chat history', async () => {
    const imageSource = {
      uri: 'https://api.test/api/v1/chat/attachments/photo-1/content',
      headers: { Authorization: 'Bearer test-token' },
    };
    const api = {
      getMessages: jest.fn().mockResolvedValue({
        messages: [
          {
            id: '019b1111-1111-7111-8111-111111111111',
            clientMessageId: '11111111-1111-4111-8111-111111111111',
            role: 'user',
            status: 'completed',
            content: '请帮我看看这张照片。',
            sources: [],
            attachments: [
              {
                id: '019b2222-2222-7222-8222-222222222222',
                kind: 'photo',
                filename: 'report.jpg',
                mimeType: 'image/jpeg',
                sizeBytes: 128,
              },
            ],
            createdAt: '2026-08-16T05:00:00.000Z',
            completedAt: null,
          },
        ],
        nextBefore: null,
      }),
      getChatAttachmentImageSource: jest.fn().mockResolvedValue(imageSource),
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
      await Promise.resolve();
    });

    expect(api.getChatAttachmentImageSource).toHaveBeenCalledWith(
      '019b2222-2222-7222-8222-222222222222',
    );
    expect(
      tree!.root.findByProps({ accessibilityLabel: '上传的图片' }).props
        .source,
    ).toEqual(imageSource);
    expect(tree!.root.findAllByProps({ children: 'report.jpg' })).toHaveLength(0);
    act(() => tree!.unmount());
  });

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
          _attachmentId: string | null,
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
    expect(
      tree!.root.findByProps({ accessibilityLabel: '报告解读' }).props
        .disabled,
    ).toBe(true);

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
