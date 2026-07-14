import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { ApiClient, errorMessage, SessionExpiredError } from './src/api/client';
import type { User } from './src/api/types';
import { AuthScreen } from './src/auth/AuthScreen';
import { secureTokenStore } from './src/auth/token-store';
import { ChatScreen } from './src/chat/ChatScreen';
import { API_URL } from './src/config';

type AppState =
  | { kind: 'loading' }
  | { kind: 'signed-out'; message?: string }
  | { kind: 'signed-in'; user: User }
  | { kind: 'startup-error'; message: string };

export default function App() {
  const [state, setState] = useState<AppState>({ kind: 'loading' });
  const [startupAttempt, setStartupAttempt] = useState(0);
  const apiRef = useRef<ApiClient | null>(null);

  if (apiRef.current === null) {
    apiRef.current = new ApiClient(API_URL, secureTokenStore, () => {
      setState({ kind: 'signed-out', message: '登录已过期，请重新登录。' });
    });
  }

  const api = apiRef.current;

  useEffect(() => {
    let active = true;

    async function restoreSession() {
      try {
        const tokens = await secureTokenStore.load();

        if (tokens === null) {
          if (active) setState({ kind: 'signed-out' });
          return;
        }

        const user = await api.me();
        if (active) setState({ kind: 'signed-in', user });
      } catch (error) {
        if (active) {
          if (error instanceof SessionExpiredError) {
            setState({ kind: 'signed-out', message: error.message });
          } else {
            setState({ kind: 'startup-error', message: errorMessage(error) });
          }
        }
      }
    }

    void restoreSession();

    return () => {
      active = false;
    };
  }, [api, startupAttempt]);

  if (state.kind === 'loading') {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color="#15966A" size="large" />
        <Text style={styles.loadingText}>正在恢复登录状态…</Text>
      </View>
    );
  }

  if (state.kind === 'startup-error') {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorTitle}>启动失败</Text>
        <Text style={styles.errorText}>{state.message}</Text>
        <Pressable
          accessibilityRole="button"
          onPress={() => {
            setState({ kind: 'loading' });
            setStartupAttempt((attempt) => attempt + 1);
          }}
          style={styles.actionButton}
        >
          <Text style={styles.actionButtonText}>重试</Text>
        </Pressable>
      </View>
    );
  }

  if (state.kind === 'signed-out') {
    return (
      <AuthScreen
        api={api}
        initialMessage={state.message}
        onAuthenticated={(user) => setState({ kind: 'signed-in', user })}
      />
    );
  }

  return (
    <ChatScreen
      api={api}
      user={state.user}
      onSignedOut={() => setState({ kind: 'signed-out' })}
    />
  );
}

const styles = StyleSheet.create({
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 28,
    backgroundColor: '#F7F9F8',
  },
  loadingText: {
    marginTop: 14,
    color: '#65716B',
    fontSize: 15,
  },
  errorTitle: {
    color: '#712B2B',
    fontSize: 22,
    fontWeight: '700',
  },
  errorText: {
    marginTop: 10,
    color: '#65716B',
    fontSize: 15,
    lineHeight: 22,
    textAlign: 'center',
  },
  actionButton: {
    marginTop: 22,
    paddingHorizontal: 22,
    paddingVertical: 12,
    borderRadius: 22,
    backgroundColor: '#15966A',
  },
  actionButtonText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '600',
  },
});
