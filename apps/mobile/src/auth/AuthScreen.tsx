import { StatusBar as ExpoStatusBar } from 'expo-status-bar';
import { useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { type ApiClient, errorMessage } from '../api/client';
import type { User } from '../api/types';

type AuthMode = 'login' | 'register';

type Props = {
  api: ApiClient;
  initialMessage?: string;
  onAuthenticated: (user: User) => void;
};

export function AuthScreen({ api, initialMessage, onAuthenticated }: Props) {
  const [mode, setMode] = useState<AuthMode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(initialMessage ?? null);

  const canSubmit =
    username.trim().length >= 3 && password.length >= 8 && !submitting;

  async function submit() {
    if (!canSubmit) return;

    setSubmitting(true);
    setError(null);
    try {
      const user =
        mode === 'login'
          ? await api.login(username.trim(), password)
          : await api.register(username.trim(), password);
      onAuthenticated(user);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  function switchMode(nextMode: AuthMode) {
    setMode(nextMode);
    setPassword('');
    setError(null);
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      style={styles.screen}
    >
      <View style={styles.card}>
        <Text style={styles.brand}>安糖心语</Text>
        <Text style={styles.intro}>在这里，慢慢说出你对低血糖的担心。</Text>

        <View style={styles.modeRow}>
          <Pressable
            accessibilityRole="button"
            onPress={() => switchMode('login')}
            style={[styles.modeButton, mode === 'login' && styles.modeButtonActive]}
          >
            <Text style={[styles.modeText, mode === 'login' && styles.modeTextActive]}>
              登录
            </Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            onPress={() => switchMode('register')}
            style={[styles.modeButton, mode === 'register' && styles.modeButtonActive]}
          >
            <Text
              style={[styles.modeText, mode === 'register' && styles.modeTextActive]}
            >
              注册
            </Text>
          </Pressable>
        </View>

        <TextInput
          autoCapitalize="none"
          autoCorrect={false}
          maxLength={32}
          onChangeText={setUsername}
          placeholder="用户名（至少 3 个字符）"
          placeholderTextColor="#8A918E"
          style={styles.input}
          value={username}
        />
        <TextInput
          autoCapitalize="none"
          maxLength={128}
          onChangeText={setPassword}
          onSubmitEditing={() => void submit()}
          placeholder="密码（至少 8 个字符）"
          placeholderTextColor="#8A918E"
          secureTextEntry
          style={styles.input}
          value={password}
        />

        {error === null ? null : <Text style={styles.error}>{error}</Text>}

        <Pressable
          accessibilityRole="button"
          disabled={!canSubmit}
          onPress={() => void submit()}
          style={({ pressed }) => [
            styles.submitButton,
            !canSubmit && styles.submitButtonDisabled,
            pressed && canSubmit && styles.submitButtonPressed,
          ]}
        >
          {submitting ? (
            <ActivityIndicator color="#FFFFFF" />
          ) : (
            <Text style={styles.submitText}>{mode === 'login' ? '登录' : '注册'}</Text>
          )}
        </Pressable>
      </View>
      <ExpoStatusBar style="dark" />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    justifyContent: 'center',
    paddingTop: Platform.OS === 'android' ? StatusBar.currentHeight : 0,
    paddingHorizontal: 24,
    backgroundColor: '#F7F9F8',
  },
  card: {
    padding: 24,
    borderRadius: 26,
    backgroundColor: '#FFFFFF',
  },
  brand: {
    color: '#15201B',
    fontSize: 28,
    fontWeight: '700',
  },
  intro: {
    marginTop: 9,
    color: '#65716B',
    fontSize: 15,
    lineHeight: 22,
  },
  modeRow: {
    flexDirection: 'row',
    marginTop: 26,
    marginBottom: 14,
    padding: 4,
    borderRadius: 20,
    backgroundColor: '#F1F5F3',
  },
  modeButton: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: 9,
    borderRadius: 16,
  },
  modeButtonActive: {
    backgroundColor: '#FFFFFF',
  },
  modeText: {
    color: '#6A756F',
    fontSize: 15,
    fontWeight: '600',
  },
  modeTextActive: {
    color: '#157D5B',
  },
  input: {
    height: 50,
    marginTop: 12,
    paddingHorizontal: 16,
    color: '#17211C',
    fontSize: 16,
    borderRadius: 16,
    backgroundColor: '#F2F5F3',
  },
  error: {
    marginTop: 14,
    color: '#A43B3B',
    fontSize: 14,
    lineHeight: 20,
  },
  submitButton: {
    height: 50,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 20,
    borderRadius: 25,
    backgroundColor: '#15966A',
  },
  submitButtonDisabled: {
    backgroundColor: '#B9C4BF',
  },
  submitButtonPressed: {
    opacity: 0.78,
  },
  submitText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
  },
});
