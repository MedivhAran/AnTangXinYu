import React, { useMemo } from 'react';
import {
  Dimensions,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LineChart } from 'react-native-chart-kit';
import { ArrowLeft01Icon } from '@hugeicons/core-free-icons';

import { AppIcon } from '../ui/icon';
import { colors, radii, spacing, typefaces } from '../ui/theme';
import type { HealthBundle } from '../services/GadgetbridgeService';

const W = Dimensions.get('window').width;

type Props = {
  visible: boolean;
  onClose: () => void;
  bundle: HealthBundle | null;
};

export function HealthDashboardScreen({ visible, onClose, bundle }: Props) {
  const hrSeries = useMemo(() => {
    const arr = bundle?.heartRates ?? [];
    return arr.slice(-30).map((r) => r.heartRate);
  }, [bundle]);

  const stepSeries = useMemo(() => {
    const arr = bundle?.steps ?? [];
    return arr.slice(-30).map((r) => r.steps);
  }, [bundle]);

  const avgHr = hrSeries.length
    ? Math.round(hrSeries.reduce((a, b) => a + b, 0) / hrSeries.length)
    : 0;
  const totalSteps = stepSeries.reduce((a, b) => a + b, 0);
  const latestSpo2 = bundle?.spo2?.slice(-1)[0]?.spo2;
  const latestRhr = bundle?.restingHeartRates?.slice(-1)[0]?.restingHeartRate;

  const hrCount = bundle?.heartRates.length ?? 0;
  const stepCount = bundle?.steps.length ?? 0;
  const spo2Count = bundle?.spo2.length ?? 0;
  const rhrCount = bundle?.restingHeartRates.length ?? 0;

  return (
    <Modal
      animationType="slide"
      onRequestClose={onClose}
      statusBarTranslucent
      visible={visible}
    >
      <SafeAreaView style={s.root} edges={['top', 'bottom']}>
        <View style={s.header}>
          <Pressable
            accessibilityLabel="返回"
            accessibilityRole="button"
            onPress={onClose}
            style={({ pressed }) => [s.backBtn, pressed && { opacity: 0.6 }]}
          >
            <AppIcon color={colors.ink} icon={ArrowLeft01Icon} size={22} />
          </Pressable>
          <Text style={s.headerTitle}>健康看板</Text>
          <View style={s.headerSpacer} />
        </View>

        {!bundle ? (
          <View style={s.center}>
            <Text style={s.hint}>
              还没有数据。{'\n'}回聊天页点 Gadgetbridge 横幅，导入 .db 或 .csv 文件。
            </Text>
          </View>
        ) : (
          <ScrollView
            contentContainerStyle={s.scrollBody}
            showsVerticalScrollIndicator={false}
          >
            <View style={s.cardRow}>
              <Card label="平均心率" value={`${avgHr} bpm`} sub={`${hrCount} 条`} />
              <Card label="总步数" value={`${totalSteps}`} sub={`${stepCount} 条`} />
            </View>
            <View style={s.cardRow}>
              <Card
                label="最新血氧"
                value={latestSpo2 ? `${latestSpo2}%` : '—'}
                sub={`${spo2Count} 条`}
              />
              <Card
                label="静息心率"
                value={latestRhr ? `${latestRhr} bpm` : '—'}
                sub={`${rhrCount} 条`}
              />
            </View>

            <Text style={s.section}>心率趋势（最近 30 条）</Text>
            {hrSeries.length > 0 ? (
              <LineChart
                data={{
                  labels: hrSeries.map(() => ''),
                  datasets: [{ data: hrSeries }],
                }}
                width={W - spacing.md * 2}
                height={180}
                chartConfig={chartConfig}
                bezier
                style={s.chart}
              />
            ) : (
              <Text style={s.hint}>无心率数据</Text>
            )}

            <Text style={s.section}>步数趋势（最近 30 条）</Text>
            {stepSeries.length > 0 ? (
              <LineChart
                data={{
                  labels: stepSeries.map(() => ''),
                  datasets: [{ data: stepSeries }],
                }}
                width={W - spacing.md * 2}
                height={180}
                chartConfig={chartConfig}
                bezier
                style={s.chart}
              />
            ) : (
              <Text style={s.hint}>无步数数据</Text>
            )}
          </ScrollView>
        )}
      </SafeAreaView>
    </Modal>
  );
}

function Card({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <View style={s.card}>
      <Text style={s.cardLabel}>{label}</Text>
      <Text style={s.cardValue}>{value}</Text>
      {sub ? <Text style={s.cardSub}>{sub}</Text> : null}
    </View>
  );
}

const chartConfig = {
  backgroundGradientFrom: '#fff',
  backgroundGradientTo: '#fff',
  decimalPlaces: 0,
  color: (o = 1) => `rgba(59,130,246,${o})`,
  labelColor: (o = 1) => `rgba(0,0,0,${o})`,
  propsForDots: { r: '2' },
};

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f7f8fa' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  backBtn: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
  },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 17,
  },
  headerSpacer: { width: 40 },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  hint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 14,
    textAlign: 'center',
    lineHeight: 22,
  },
  scrollBody: { padding: spacing.md, paddingBottom: 40 },
  section: {
    fontSize: 15,
    fontFamily: typefaces.sansMedium,
    color: colors.ink,
    marginTop: spacing.lg,
    marginBottom: spacing.sm,
  },
  cardRow: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm },
  card: {
    flex: 1,
    backgroundColor: '#fff',
    borderRadius: radii.lg,
    padding: spacing.md,
  },
  cardLabel: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 12 },
  cardValue: {
    fontSize: 22,
    fontFamily: typefaces.sansMedium,
    color: colors.ink,
    marginTop: 6,
  },
  cardSub: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11,
    marginTop: 2,
  },
  chart: { borderRadius: radii.lg },
});