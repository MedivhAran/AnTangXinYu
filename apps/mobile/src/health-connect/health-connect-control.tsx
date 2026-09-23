import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, radii, spacing, typefaces } from '../ui/theme';
import type { HealthConnectState } from './use-health-connect';

type Props = Omit<
  HealthConnectState,
  | 'backgroundStatus'
  | 'backgroundError'
  | 'backgroundEnabling'
  | 'backgroundDisabling'
> & {
  onConnect: () => void;
  onSync: () => void;
};

function formatLastSync(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

export function HealthConnectControl({
  checking,
  connected,
  syncing,
  progress,
  lastSyncedAt,
  error,
  onConnect,
  onSync,
}: Props) {
  const busy = checking || syncing;
  const status = checking
    ? '正在检查手环连接'
    : progress ??
    (lastSyncedAt === null
      ? connected
        ? '手环已连接'
        : '连接 Health Connect 后可同步手环数据'
      : `上次同步 ${formatLastSync(lastSyncedAt)}`);

  return (
    <View style={styles.container}>
      <View style={styles.statusContainer}>
        <View style={styles.statusLine}>
          {busy ? <ActivityIndicator color="#15966A" size="small" /> : null}
          <Text style={styles.status}>{status}</Text>
        </View>
        {error === null ? null : (
          <Text selectable style={styles.error}>
            {error}
          </Text>
        )}
      </View>
      <Pressable
        accessibilityLabel={connected ? '同步手环' : '连接手环'}
        accessibilityRole="button"
        disabled={busy}
        onPress={connected ? onSync : onConnect}
        style={({ pressed }) => [
          styles.button,
          busy && styles.disabledButton,
          pressed && !busy && styles.pressedButton,
        ]}
      >
        <Text style={styles.buttonText}>{connected ? '立即同步' : '连接手环'}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  statusContainer: { flex: 1, gap: spacing.xxs },
  statusLine: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  status: {
    flex: 1,
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 11,
    lineHeight: 16,
  },
  error: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 10,
    lineHeight: 14,
  },
  button: {
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.ink,
  },
  buttonText: {
    color: colors.paper,
    fontFamily: typefaces.sansMedium,
    fontSize: 11,
  },
  disabledButton: { opacity: 0.5 },
  pressedButton: { opacity: 0.75 },
});


