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
import type {
  HealthBundle,
  SleepSession,
  SleepStageName,
} from '../services/GadgetbridgeService';

const W = Dimensions.get('window').width;

/** 心率趋势最多画这么多点，否则手机端 LineChart 会明显掉帧。 */
const HEART_RATE_TREND_POINTS = 240;

const STAGE_STYLE: Record<SleepStageName, { color: string; label: string }> = {
  awake: { color: '#F2B441', label: '清醒' },
  awake_in_bed: { color: '#F2B441', label: '卧床清醒' },
  light: { color: '#7AA6E8', label: '浅睡' },
  deep: { color: '#3B5BA5', label: '深睡' },
  rem: { color: '#8E7CC3', label: 'REM' },
  sleeping: { color: '#B0B7C3', label: '睡眠' },
  out_of_bed: { color: '#D8DEE9', label: '离床' },
  unknown: { color: '#E3E7ED', label: '未知' },
};

type Props = {
  visible: boolean;
  onClose: () => void;
  bundle: HealthBundle | null;
};

function formatClock(iso: string): string {
  const date = new Date(iso);
  const hours = `${date.getHours()}`.padStart(2, '0');
  const minutes = `${date.getMinutes()}`.padStart(2, '0');
  return `${hours}:${minutes}`;
}

function formatDay(iso: string): string {
  const date = new Date(iso);
  return `${date.getMonth() + 1}/${date.getDate()}`;
}

function formatDuration(minutes: number): string {
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  return hours === 0 ? `${rest} 分` : `${hours} 小时 ${rest} 分`;
}

function stageMinutes(session: SleepSession, stage: SleepStageName): number {
  return session.stages
    .filter((slice) => slice.stage === stage)
    .reduce(
      (total, slice) => total + (Date.parse(slice.endTime) - Date.parse(slice.startTime)) / 60000,
      0,
    );
}

export function HealthDashboardScreen({ visible, onClose, bundle }: Props) {
  const totals = bundle?.totals;

  const heartRateTrend = useMemo(
    () => (bundle?.heartRates ?? []).slice(-HEART_RATE_TREND_POINTS),
    [bundle],
  );

  const dailySteps = useMemo(() => {
    const days = bundle?.daily ?? [];
    // 步数全为 0 的日子画出来是一条直线，没有信息量，直接跳过
    return days.filter((day) => day.steps > 0);
  }, [bundle]);

  const sleepSessions = useMemo(
    () => [...(bundle?.sleep ?? [])].sort((left, right) => left.startTime.localeCompare(right.startTime)),
    [bundle],
  );

  const usedStages = useMemo(() => {
    const names = new Set<SleepStageName>();
    for (const session of sleepSessions) {
      for (const slice of session.stages) names.add(slice.stage);
    }
    return [...names];
  }, [sleepSessions]);

  const hasData = bundle !== null && totals !== undefined;

  return (
    <Modal animationType="slide" onRequestClose={onClose} statusBarTranslucent visible={visible}>
      <SafeAreaView style={s.root} edges={['top', 'bottom']}>
        <View style={s.header}>
          <Pressable
            accessibilityLabel="返回"
            accessibilityRole="button"
            onPress={onClose}
            style={({ pressed }) => [s.backButton, pressed && s.pressed]}
          >
            <AppIcon color={colors.ink} icon={ArrowLeft01Icon} size={22} />
          </Pressable>
          <Text style={s.headerTitle}>本次导入预览</Text>
          <View style={s.headerSpacer} />
        </View>

        {!hasData ? (
          <View style={s.center}>
            <Text style={s.hint}>
              本次尚未选择 Gadgetbridge 导出文件。已上传的历史记录可在健康档案查看。
            </Text>
          </View>
        ) : (
          <ScrollView contentContainerStyle={s.body} showsVerticalScrollIndicator={false}>
            <Text style={s.source}>
              {bundle.device === null
                ? '数据来源：Gadgetbridge 导出'
                : `数据来源：${bundle.device.manufacturer ?? ''} ${bundle.device.model ?? ''}`.trim()}
              {totals.rangeStart !== null && totals.rangeEnd !== null
                ? `\n${formatDay(totals.rangeStart)} – ${formatDay(totals.rangeEnd)}，共 ${totals.dayCount} 天`
                : ''}
            </Text>

            <View style={s.cardRow}>
              <Card
                label="平均心率"
                value={totals.heartRateAvg === null ? '—' : `${totals.heartRateAvg} bpm`}
                sub={`${totals.heartRateCount} 条样本`}
              />
              <Card
                label="心率范围"
                value={
                  totals.heartRateMin === null || totals.heartRateMax === null
                    ? '—'
                    : `${totals.heartRateMin}–${totals.heartRateMax}`
                }
                sub="最低 – 最高 bpm"
              />
            </View>

            <View style={s.cardRow}>
              <Card
                label="总步数"
                value={totals.totalSteps.toLocaleString()}
                sub={`${totals.dayCount} 天合计`}
              />
              <Card
                label="总距离"
                value={`${(totals.totalDistanceMeters / 1000).toFixed(1)} km`}
                sub="按 0.73 米/步实测校准"
              />
            </View>

            <View style={s.cardRow}>
              <Card
                label="最低血氧"
                value={totals.spo2Min === null ? '—' : `${totals.spo2Min}%`}
                sub={`平均 ${totals.spo2Avg ?? '—'}%，${totals.spo2Count} 条`}
              />
              <Card
                label="睡眠时长"
                value={formatDuration(totals.sleepMinutes)}
                sub={`${totals.sleepSessionCount} 晚`}
              />
            </View>

            <View style={s.cardRow}>
              <Card
                label="静息心率"
                value={
                  totals.restingHeartRateMin === null
                    ? '—'
                    : `${totals.restingHeartRateMin} bpm`
                }
                sub={`时段最低，${totals.restingHeartRateCount} 条`}
              />
              <Card
                label="平均压力"
                value={totals.stressAvg === null ? '—' : `${totals.stressAvg}`}
                sub={
                  totals.stressCount === 0
                    ? '本次导出没有压力数据'
                    : `${totals.stressCount} 条`
                }
              />
            </View>

            <Section title="每日步数" />
            {dailySteps.length >= 2 ? (
              <LineChart
                data={{
                  labels: dailySteps.map((day, index) =>
                    index % 3 === 0 ? formatDay(day.dayStart) : '',
                  ),
                  datasets: [{ data: dailySteps.map((day) => day.steps) }],
                }}
                width={W - spacing.md * 2}
                height={180}
                chartConfig={chartConfig}
                bezier
                fromZero
                withDots={false}
                style={s.chart}
              />
            ) : (
              <Text style={s.hint}>可用的每日步数不足两天</Text>
            )}

            <Section title={`心率趋势（最近 ${heartRateTrend.length} 条原始样本）`} />
            {heartRateTrend.length >= 2 ? (
              <LineChart
                data={{
                  labels: heartRateTrend.map((_, index) =>
                    index % Math.ceil(heartRateTrend.length / 4) === 0
                      ? formatClock(heartRateTrend[index].timestamp)
                      : '',
                  ),
                  datasets: [{ data: heartRateTrend.map((item) => item.heartRate) }],
                }}
                width={W - spacing.md * 2}
                height={180}
                chartConfig={chartConfig}
                bezier
                fromZero={false}
                withDots={false}
                style={s.chart}
              />
            ) : (
              <Text style={s.hint}>没有心率样本</Text>
            )}

            <Section title="睡眠阶段" />
            {sleepSessions.length === 0 ? (
              <Text style={s.hint}>本次导出没有睡眠阶段数据</Text>
            ) : (
              <>
                <View style={s.legend}>
                  {usedStages.map((stage) => (
                    <View key={stage} style={s.legendItem}>
                      <View style={[s.legendDot, { backgroundColor: STAGE_STYLE[stage].color }]} />
                      <Text style={s.legendText}>{STAGE_STYLE[stage].label}</Text>
                    </View>
                  ))}
                </View>
                {sleepSessions.map((session) => (
                  <SleepRow key={session.startTime} session={session} />
                ))}
              </>
            )}

            <Section title="本次导入说明" />
            <Text style={s.note}>
              · 已上传后端：心率、步数、血氧、静息心率、距离、睡眠阶段。
              {'\n'}· 仅本机展示：压力、卡路里、睡眠评分与深睡时长——后端
              WearableRecordType 目前没有这几类记录，加上需要改后端和数据库迁移。
              {'\n'}· 心率与血氧是原始点测量，步数/距离/卡路里按 15 分钟分桶，
              静息心率取每小时最低值。
            </Text>
          </ScrollView>
        )}
      </SafeAreaView>
    </Modal>
  );
}

function Section({ title }: { title: string }) {
  return <Text style={s.section}>{title}</Text>;
}

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <View style={s.card}>
      <Text style={s.cardLabel}>{label}</Text>
      <Text style={s.cardValue}>{value}</Text>
      {sub === undefined ? null : <Text style={s.cardSub}>{sub}</Text>}
    </View>
  );
}

/** 一晚睡眠的阶段时间轴，横向按真实时长比例铺开。 */
function SleepRow({ session }: { session: SleepSession }) {
  const awakeMinutes = stageMinutes(session, 'awake') + stageMinutes(session, 'awake_in_bed');
  const asleepMinutes = stageMinutes(session, 'light') + stageMinutes(session, 'deep') + stageMinutes(session, 'rem') + stageMinutes(session, 'sleeping');

  return (
    <View style={s.sleepRow}>
      <View style={s.sleepHeader}>
        <Text style={s.sleepDate}>{formatDay(session.startTime)}</Text>
        <Text style={s.sleepMeta}>
          {formatClock(session.startTime)}–{formatClock(session.endTime)}　
          {formatDuration(asleepMinutes)}
          {awakeMinutes >= 1 ? `（清醒 ${Math.round(awakeMinutes)} 分）` : ''}
          {session.score === null ? '' : `　评分 ${session.score}`}
        </Text>
      </View>
      <View style={s.timeline}>
        {session.stages.map((slice) => {
          const minutes = Math.max(
            1,
            (Date.parse(slice.endTime) - Date.parse(slice.startTime)) / 60000,
          );
          return (
            <View
              key={`${slice.startTime}-${slice.stage}`}
              style={{
                flex: minutes,
                backgroundColor: STAGE_STYLE[slice.stage].color,
              }}
            />
          );
        })}
      </View>
      {session.minOxygenSaturation === null && session.deepMinutes === null ? null : (
        <Text style={s.sleepExtra}>
          {session.deepMinutes === null ? '' : `深睡 ${session.deepMinutes} 分`}
          {session.deepMinutes !== null && session.minOxygenSaturation !== null ? '　' : ''}
          {session.minOxygenSaturation === null
            ? ''
            : `夜间最低血氧 ${session.minOxygenSaturation}%`}
        </Text>
      )}
    </View>
  );
}

const chartConfig = {
  backgroundGradientFrom: '#ffffff',
  backgroundGradientTo: '#ffffff',
  decimalPlaces: 0,
  color: (opacity = 1) => `rgba(10,124,107,${opacity})`,
  labelColor: (opacity = 1) => `rgba(107,114,128,${opacity})`,
  propsForDots: { r: '0' },
};

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f7f8fa' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  backButton: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
  },
  pressed: { opacity: 0.6 },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 17,
  },
  headerSpacer: { width: 40 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  hint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 13,
    textAlign: 'center',
    lineHeight: 21,
  },
  body: { padding: spacing.md, paddingBottom: 40 },
  source: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
  },
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
    backgroundColor: '#ffffff',
    borderRadius: radii.lg,
    padding: spacing.md,
  },
  cardLabel: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 12 },
  cardValue: {
    fontSize: 21,
    fontFamily: typefaces.sansMedium,
    color: colors.ink,
    marginTop: 6,
  },
  cardSub: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11,
    marginTop: 2,
    lineHeight: 15,
  },
  chart: { borderRadius: radii.lg },
  legend: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md, marginBottom: spacing.sm },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  legendDot: { width: 10, height: 10, borderRadius: 3 },
  legendText: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 11 },
  sleepRow: {
    backgroundColor: '#ffffff',
    borderRadius: radii.lg,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  sleepHeader: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  sleepDate: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
    minWidth: 40,
  },
  sleepMeta: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 11, flex: 1 },
  timeline: {
    flexDirection: 'row',
    height: 18,
    borderRadius: 5,
    overflow: 'hidden',
    marginTop: spacing.sm,
  },
  sleepExtra: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 11,
    marginTop: 6,
  },
  note: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 20,
  },
});
