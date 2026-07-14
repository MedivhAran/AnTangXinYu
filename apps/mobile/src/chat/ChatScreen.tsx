import { randomUUID } from 'expo-crypto';
import { StatusBar as ExpoStatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { ApiClient, ApiError, errorMessage } from '../api/client';
import type { ChatMessage, User } from '../api/types';
import { ActivityStatus } from './ActivityStatus';
import { chatReducer, initialChatState } from './chat-state';

type Props = {
  api: ApiClient;
  user: User;
  onSignedOut: () => void;
};

export function ChatScreen({ api, user, onSignedOut }: Props) {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [draft, setDraft] = useState('');
  const listRef = useRef<FlatList<ChatMessage>>(null);
  const streamController = useRef<AbortController | null>(null);

  const loadLatest = useCallback(async () => {
    dispatch({ type: 'history-loading' });
    try {
      const page = await api.getMessages();
      dispatch({
        type: 'history-replaced',
        messages: page.messages,
        nextBefore: page.nextBefore,
      });
    } catch (error) {
      dispatch({ type: 'history-failed', message: errorMessage(error) });
    }
  }, [api]);

  useEffect(() => {
    void loadLatest();
    return () => streamController.current?.abort();
  }, [loadLatest]);

  const lastMessage = state.messages[state.messages.length - 1];
  useEffect(() => {
    if (lastMessage === undefined) return;
    requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
  }, [lastMessage, state.activity]);

  const busy = state.sending || state.historyLoading;

  async function loadOlder() {
    if (state.nextBefore === null || state.historyLoading) return;
    dispatch({ type: 'history-loading' });
    try {
      const page = await api.getMessages(state.nextBefore);
      dispatch({
        type: 'history-prepended',
        messages: page.messages,
        nextBefore: page.nextBefore,
      });
    } catch (error) {
      dispatch({ type: 'history-failed', message: errorMessage(error) });
    }
  }

  async function sendContent(rawContent: string) {
    const content = rawContent.trim();
    if (content.length === 0 || busy || streamController.current !== null) return;

    const clientMessageId = randomUUID();
    const controller = new AbortController();
    streamController.current = controller;
    setDraft('');
    dispatch({
      type: 'send-started',
      clientMessageId,
      content,
      now: new Date().toISOString(),
    });

    try {
      await api.streamMessage(
        clientMessageId,
        content,
        (event) => dispatch({ type: 'stream-event', event }),
        controller.signal,
      );
    } catch (error) {
      if (controller.signal.aborted) {
        dispatch({ type: 'send-cancelled' });
      } else if (error instanceof ApiError && error.code === 'duplicate_client_message') {
        dispatch({ type: 'send-rejected', clientMessageId, message: null });
        await loadLatest();
      } else if (error instanceof ApiError && error.code === 'active_agent_run') {
        dispatch({
          type: 'send-rejected',
          clientMessageId,
          message: '上一条回复仍在生成，请刷新聊天记录查看状态。',
        });
      } else {
        dispatch({ type: 'transport-failed', message: errorMessage(error) });
      }
    } finally {
      if (streamController.current === controller) streamController.current = null;
    }
  }

  async function logout() {
    streamController.current?.abort();
    try {
      await api.logout();
      onSignedOut();
    } catch (error) {
      dispatch({ type: 'history-failed', message: errorMessage(error) });
    }
  }

  function retry(index: number) {
    for (let current = index - 1; current >= 0; current -= 1) {
      const message = state.messages[current];
      if (message.role === 'user') {
        void sendContent(message.content);
        return;
      }
    }
    dispatch({ type: 'history-failed', message: '找不到这次回复对应的用户消息。' });
  }

  function renderMessage(message: ChatMessage, index: number) {
    const assistant = message.role === 'assistant';
    const canRetry =
      assistant && (message.status === 'failed' || message.status === 'cancelled');

    const showBubble = message.content.length > 0 || message.status !== 'generating';
    const activity =
      assistant && message.status === 'generating' ? state.activity : null;

    return (
      <View
        style={[
          styles.messageRow,
          assistant ? styles.assistantMessageRow : styles.userMessageRow,
        ]}
      >
        <View
          style={[
            styles.messageContent,
            assistant ? styles.assistantContent : undefined,
          ]}
        >
          {showBubble ? (
            <View
              style={[
                styles.bubble,
                assistant ? styles.assistantBubble : styles.userBubble,
              ]}
            >
              <Text style={styles.messageText}>{message.content || '无内容'}</Text>
              {message.status === 'failed' ? (
                <Text style={styles.failedStatus}>回复失败</Text>
              ) : null}
              {message.status === 'cancelled' ? (
                <Text style={styles.cancelledStatus}>回复已取消</Text>
              ) : null}
              {canRetry ? (
                <Pressable
                  accessibilityRole="button"
                  disabled={busy}
                  onPress={() => retry(index)}
                  style={styles.retryButton}
                >
                  <Text style={styles.retryText}>重新发送</Text>
                </Pressable>
              ) : null}
            </View>
          ) : null}
          {activity === null ? null : <ActivityStatus phase={activity} />}
        </View>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      style={styles.screen}
    >
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>安糖心语</Text>
          <Text style={styles.username}>{user.username}</Text>
        </View>
        <View style={styles.headerActions}>
          <Pressable
            accessibilityRole="button"
            disabled={state.pendingClientMessageId !== null || state.historyLoading}
            onPress={() => void loadLatest()}
          >
            <Text
              style={[
                styles.headerAction,
                (state.pendingClientMessageId !== null || state.historyLoading) &&
                  styles.headerActionDisabled,
              ]}
            >
              刷新
            </Text>
          </Pressable>
          <Pressable accessibilityRole="button" onPress={() => void logout()}>
            <Text style={styles.headerAction}>退出</Text>
          </Pressable>
        </View>
      </View>

      {state.error === null ? null : (
        <Pressable
          accessibilityRole="button"
          onPress={() => dispatch({ type: 'clear-error' })}
          style={styles.errorBanner}
        >
          <Text style={styles.errorText}>{state.error}</Text>
          <Text style={styles.dismissText}>关闭</Text>
        </Pressable>
      )}

      <FlatList
        ref={listRef}
        contentContainerStyle={styles.messageList}
        data={state.messages}
        keyboardShouldPersistTaps="handled"
        keyExtractor={(item) => item.id}
        ListEmptyComponent={
          state.historyLoading ? (
            <View style={styles.emptyState}>
              <ActivityIndicator color="#15966A" />
            </View>
          ) : (
            <View style={styles.emptyState}>
              <Text style={styles.emptyTitle}>今天感觉怎么样？</Text>
              <Text style={styles.emptyText}>可以从最近一次担心低血糖的时刻说起。</Text>
            </View>
          )
        }
        ListHeaderComponent={
          state.nextBefore === null ? null : (
            <Pressable
              accessibilityRole="button"
              disabled={state.historyLoading}
              onPress={() => void loadOlder()}
              style={styles.olderButton}
            >
              <Text style={styles.olderText}>
                {state.historyLoading ? '正在加载…' : '加载更早消息'}
              </Text>
            </Pressable>
          )
        }
        renderItem={({ item, index }) => renderMessage(item, index)}
      />

      <View style={styles.composer}>
        <TextInput
          editable={!busy}
          maxLength={2000}
          multiline
          onChangeText={setDraft}
          placeholder={busy ? '请稍候…' : '说点什么……'}
          placeholderTextColor="#8A918E"
          style={styles.input}
          value={draft}
        />
        <Pressable
          accessibilityLabel="发送消息"
          accessibilityRole="button"
          disabled={draft.trim().length === 0 || busy}
          onPress={() => void sendContent(draft)}
          style={({ pressed }) => [
            styles.sendButton,
            (draft.trim().length === 0 || busy) && styles.sendButtonDisabled,
            pressed && !busy && styles.sendButtonPressed,
          ]}
        >
          <Text style={styles.sendButtonText}>发送</Text>
        </Pressable>
      </View>

      <ExpoStatusBar style="dark" />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    paddingTop: Platform.OS === 'android' ? StatusBar.currentHeight : 0,
    backgroundColor: '#F7F9F8',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingTop: 14,
    paddingBottom: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#DDE3E0',
    backgroundColor: '#FFFFFF',
  },
  title: { color: '#15201B', fontSize: 20, fontWeight: '700' },
  username: { marginTop: 2, color: '#65716B', fontSize: 12 },
  headerActions: { flexDirection: 'row', gap: 18 },
  headerAction: { color: '#157D5B', fontSize: 14, fontWeight: '600' },
  headerActionDisabled: { color: '#A7B0AC' },
  errorBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 10,
    backgroundColor: '#FBE9E7',
  },
  errorText: { flex: 1, color: '#8D3434', fontSize: 13, lineHeight: 18 },
  dismissText: { marginLeft: 12, color: '#8D3434', fontSize: 13, fontWeight: '600' },
  messageList: { flexGrow: 1, paddingHorizontal: 16, paddingVertical: 18 },
  olderButton: { alignItems: 'center', paddingVertical: 10, marginBottom: 10 },
  olderText: { color: '#65716B', fontSize: 13 },
  emptyState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
  },
  emptyTitle: { color: '#23312A', fontSize: 21, fontWeight: '600' },
  emptyText: {
    marginTop: 10,
    color: '#718078',
    fontSize: 15,
    lineHeight: 22,
    textAlign: 'center',
  },
  messageRow: { flexDirection: 'row', marginBottom: 12 },
  userMessageRow: { justifyContent: 'flex-end' },
  assistantMessageRow: { justifyContent: 'flex-start' },
  messageContent: { maxWidth: '82%' },
  assistantContent: { gap: 8 },
  bubble: { paddingHorizontal: 14, paddingVertical: 10, borderRadius: 18 },
  userBubble: { backgroundColor: '#BDEED8', borderBottomRightRadius: 5 },
  assistantBubble: { backgroundColor: '#FFFFFF', borderBottomLeftRadius: 5 },
  messageText: { color: '#17211C', fontSize: 16, lineHeight: 23 },
  failedStatus: { marginTop: 6, color: '#A43B3B', fontSize: 12 },
  cancelledStatus: { marginTop: 6, color: '#79654B', fontSize: 12 },
  retryButton: { alignSelf: 'flex-start', marginTop: 7 },
  retryText: { color: '#157D5B', fontSize: 13, fontWeight: '600' },
  composer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 10,
    paddingHorizontal: 12,
    paddingTop: 10,
    paddingBottom: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#DDE3E0',
    backgroundColor: '#FFFFFF',
  },
  input: {
    flex: 1,
    minHeight: 44,
    maxHeight: 120,
    paddingHorizontal: 15,
    paddingTop: 11,
    paddingBottom: 11,
    color: '#17211C',
    fontSize: 16,
    lineHeight: 21,
    borderRadius: 22,
    backgroundColor: '#F2F5F3',
  },
  sendButton: {
    height: 44,
    minWidth: 62,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 15,
    borderRadius: 22,
    backgroundColor: '#15966A',
  },
  sendButtonDisabled: { backgroundColor: '#B9C4BF' },
  sendButtonPressed: { opacity: 0.75 },
  sendButtonText: { color: '#FFFFFF', fontSize: 15, fontWeight: '600' },
});
