import { StatusBar as ExpoStatusBar } from 'expo-status-bar';
import { ArrowRight01Icon } from '@hugeicons/core-free-icons';
import { useRef, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Pressable,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { type ApiClient, errorMessage } from '../api/client';
import type { User } from '../api/types';
import { BrandMark } from '../ui/brand-mark';
import { AppIcon } from '../ui/icon';
import { colors, radii, spacing, typefaces } from '../ui/theme';

type AuthMode = 'login' | 'register';
type FocusedField = 'username' | 'password' | null;

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
  const [focusedField, setFocusedField] = useState<FocusedField>(null);
  const passwordInput = useRef<TextInput>(null);

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
      behavior={process.env.EXPO_OS === 'ios' ? 'padding' : 'height'}
      style={styles.screen}
    >
      <View
        accessibilityElementsHidden
        importantForAccessibility="no-hide-descendants"
        pointerEvents="none"
        style={styles.greenWash}
      />

      <ScrollView
        contentContainerStyle={styles.scrollContent}
        contentInsetAdjustmentBehavior="automatic"
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.content}>
          <View style={styles.hero}>
            <BrandMark size={54} />
            <Text accessibilityRole="header" selectable style={styles.brand}>
              安糖心语
            </Text>
            <Text selectable style={styles.heroText}>
              登录后继续对话和健康记录
            </Text>
          </View>

          <View style={styles.card}>
            <View style={styles.cardHeading}>
              <Text accessibilityRole="header" selectable style={styles.cardTitle}>
                {mode === 'login' ? '欢迎回来' : '从今天开始'}
              </Text>
              <Text selectable style={styles.cardIntro}>
                {mode === 'login'
                  ? '输入账号信息进入应用。'
                  : '创建账号后即可开始使用。'}
              </Text>
            </View>

            <View accessibilityLabel="账号方式" style={styles.modeRow}>
              <Pressable
                accessibilityLabel="切换到登录"
                accessibilityRole="tab"
                accessibilityState={{ selected: mode === 'login' }}
                hitSlop={4}
                onPress={() => switchMode('login')}
                style={({ pressed }) => [
                  styles.modeButton,
                  mode === 'login' && styles.modeButtonActive,
                  pressed && styles.modeButtonPressed,
                ]}
              >
                <Text
                  style={[
                    styles.modeText,
                    mode === 'login' && styles.modeTextActive,
                  ]}
                >
                  登录
                </Text>
              </Pressable>
              <Pressable
                accessibilityLabel="切换到注册"
                accessibilityRole="tab"
                accessibilityState={{ selected: mode === 'register' }}
                hitSlop={4}
                onPress={() => switchMode('register')}
                style={({ pressed }) => [
                  styles.modeButton,
                  mode === 'register' && styles.modeButtonActive,
                  pressed && styles.modeButtonPressed,
                ]}
              >
                <Text
                  style={[
                    styles.modeText,
                    mode === 'register' && styles.modeTextActive,
                  ]}
                >
                  注册
                </Text>
              </Pressable>
            </View>

            <View style={styles.fields}>
              <View style={styles.field}>
                <View style={styles.labelRow}>
                  <Text nativeID="username-label" style={styles.label}>
                    用户名
                  </Text>
                  <Text style={styles.requirement}>至少 3 个字符</Text>
                </View>
                <TextInput
                  accessibilityLabel="用户名"
                  autoCapitalize="none"
                  autoComplete={mode === 'login' ? 'username' : 'username-new'}
                  autoCorrect={false}
                  maxLength={32}
                  onBlur={() => setFocusedField(null)}
                  onChangeText={setUsername}
                  onFocus={() => setFocusedField('username')}
                  onSubmitEditing={() => passwordInput.current?.focus()}
                  placeholder="请输入用户名"
                  placeholderTextColor={colors.faint}
                  returnKeyType="next"
                  style={[
                    styles.input,
                    focusedField === 'username' && styles.inputFocused,
                  ]}
                  textContentType="username"
                  value={username}
                />
              </View>

              <View style={styles.field}>
                <View style={styles.labelRow}>
                  <Text nativeID="password-label" style={styles.label}>
                    密码
                  </Text>
                  <Text style={styles.requirement}>至少 8 个字符</Text>
                </View>
                <TextInput
                  ref={passwordInput}
                  accessibilityLabel="密码"
                  autoCapitalize="none"
                  autoComplete={
                    mode === 'login' ? 'current-password' : 'new-password'
                  }
                  maxLength={128}
                  onBlur={() => setFocusedField(null)}
                  onChangeText={setPassword}
                  onFocus={() => setFocusedField('password')}
                  onSubmitEditing={() => void submit()}
                  placeholder="请输入密码"
                  placeholderTextColor={colors.faint}
                  returnKeyType="done"
                  secureTextEntry
                  style={[
                    styles.input,
                    focusedField === 'password' && styles.inputFocused,
                  ]}
                  textContentType={
                    mode === 'login' ? 'password' : 'newPassword'
                  }
                  value={password}
                />
              </View>
            </View>

            {error === null ? null : (
              <View
                accessibilityLiveRegion="polite"
                accessibilityRole="alert"
                style={styles.errorBox}
              >
                <View style={styles.errorDot} />
                <Text selectable style={styles.error}>
                  {error}
                </Text>
              </View>
            )}

            <Pressable
              accessibilityLabel={
                submitting
                  ? '正在提交'
                  : mode === 'login'
                    ? '登录并进入安糖心语'
                    : '创建安糖心语账号'
              }
              accessibilityRole="button"
              accessibilityState={{ busy: submitting, disabled: !canSubmit }}
              disabled={!canSubmit}
              onPress={() => void submit()}
              style={({ pressed }) => [
                styles.submitButton,
                !canSubmit && styles.submitButtonDisabled,
                pressed && canSubmit && styles.submitButtonPressed,
              ]}
            >
              {submitting ? (
                <ActivityIndicator color={colors.paper} />
              ) : (
                <>
                  <Text style={styles.submitText}>
                    {mode === 'login' ? '进入安糖心语' : '创建账号'}
                  </Text>
                  <AppIcon color={colors.paper} icon={ArrowRight01Icon} size={20} />
                </>
              )}
            </Pressable>
          </View>
        </View>
      </ScrollView>
      <ExpoStatusBar style="dark" />
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    overflow: 'hidden',
    backgroundColor: colors.background,
  },
  scrollContent: {
    flexGrow: 1,
    justifyContent: 'center',
    paddingTop:
      (process.env.EXPO_OS === 'android' ? (StatusBar.currentHeight ?? 0) : 0) +
      spacing.xl,
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  content: {
    width: '100%',
    maxWidth: 480,
    alignSelf: 'center',
    gap: spacing.xl,
  },
  greenWash: {
    position: 'absolute',
    width: 172,
    height: 172,
    bottom: -86,
    left: -82,
    backgroundColor: colors.primarySoft,
    borderRadius: 86,
    opacity: 0.72,
  },
  hero: {
    alignItems: 'center',
    gap: spacing.xs,
    paddingHorizontal: spacing.xs,
  },
  brand: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 27,
    fontWeight: '700',
    letterSpacing: 0.6,
  },
  heroText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 14,
  },
  card: {
    gap: spacing.lg,
    padding: spacing.xl,
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: radii.xl,
    borderCurve: 'continuous',
    backgroundColor: colors.paper,
    boxShadow: '0 10px 28px rgba(23, 48, 46, 0.07)',
  },
  cardHeading: {
    gap: spacing.xxs,
  },
  cardTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 23,
    fontWeight: '700',
  },
  cardIntro: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 14,
    lineHeight: 21,
  },
  modeRow: {
    flexDirection: 'row',
    gap: spacing.xxs,
    padding: spacing.xxs,
    borderRadius: radii.md,
    borderCurve: 'continuous',
    backgroundColor: colors.paperMuted,
  },
  modeButton: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 42,
    borderRadius: radii.sm,
    borderCurve: 'continuous',
  },
  modeButtonActive: {
    backgroundColor: colors.paper,
    boxShadow: '0 2px 8px rgba(39, 60, 53, 0.08)',
  },
  modeButtonPressed: {
    opacity: 0.72,
  },
  modeText: {
    color: colors.muted,
    fontFamily: typefaces.sansMedium,
    fontSize: 15,
  },
  modeTextActive: {
    color: colors.primary,
  },
  fields: {
    gap: spacing.md,
  },
  field: {
    gap: spacing.xs,
  },
  labelRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    gap: spacing.sm,
    paddingHorizontal: 2,
  },
  label: {
    color: colors.text,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
  },
  requirement: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11,
  },
  input: {
    minHeight: 54,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 16,
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderCurve: 'continuous',
    backgroundColor: colors.white,
  },
  inputFocused: {
    borderColor: colors.primary,
    backgroundColor: colors.paper,
    boxShadow: '0 0 0 3px rgba(29, 91, 73, 0.10)',
  },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    padding: spacing.sm,
    borderRadius: radii.sm,
    backgroundColor: colors.dangerSoft,
  },
  errorDot: {
    width: 7,
    height: 7,
    marginTop: 6,
    borderRadius: radii.pill,
    backgroundColor: colors.danger,
  },
  error: {
    flex: 1,
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 14,
    lineHeight: 20,
  },
  submitButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xs,
    minHeight: 54,
    borderRadius: radii.md,
    borderCurve: 'continuous',
    backgroundColor: colors.primary,
  },
  submitButtonDisabled: {
    backgroundColor: colors.disabled,
  },
  submitButtonPressed: {
    backgroundColor: colors.primaryPressed,
    transform: [{ scale: 0.99 }],
  },
  submitText: {
    color: colors.paper,
    fontFamily: typefaces.sansMedium,
    fontSize: 16,
    letterSpacing: 0.3,
  },
});
