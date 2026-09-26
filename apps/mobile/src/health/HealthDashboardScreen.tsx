import { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Dimensions,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LineChart } from 'react-native-chart-kit';
import { ArrowLeft01Icon } from '@hugeicons/core-free-icons';

import { errorMessage, type ApiClient } from '../api/client';
import type {
  SleepStage,
  WearableDashboard,
  WearableDashboardSleepSession,
} from '../api/types';
import type { HealthBundle } from '../services/GadgetbridgeService';
import { AppIcon } from '../ui/icon';
import { colors, radii, spacing, typefaces } from '../ui/theme';

const W = Dimensions.get('window').width;
const SOURCE_LABELS: Record<string, string> = {
  'com.huami.watch.hmwatchmanager': 'Zepp · Health Connect',
  'nodomain.freeyourgadget.gadgetbridge': 'Gadgetbridge',
};
const STAGE_STYLE: Record<SleepStage['stage'], { color: string; label: string }> = {
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
  api: Pick<ApiClient, 'getWearableDashboard'>;
  refreshKey: string;
  importPreview: HealthBundle | null;
};

function formatClock(iso: string): string {
  const date = new Date(iso);
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function formatDay(iso: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    return `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;
  }
  const date = new Date(iso);
  return `${date.getMonth() + 1}/${date.getDate()}`;
}

function formatDuration(minutes: number): string {
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  return hours === 0 ? `${rest} 分` : `${hours} 小时 ${rest} 分`;
}

function stageMinutes(session: WearableDashboardSleepSession, stage: SleepStage['stage']): number {
  return session.stages
    .filter((slice) => slice.stage === stage)
    .reduce(
      (total, slice) => total + (Date.parse(slice.endTime) - Date.parse(slice.startTime)) / 60000,
      0,
    );
}

export function HealthDashboardScreen({ visible, onClose, api, refreshKey, importPreview }: Props) {
  const [dashboard, setDashboard] = useState<WearableDashboard | null>(null);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);

  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    let active = true;
    void api.getWearableDashboard(controller.signal).then(
      (value) => {
        if (active) {
          setDashboard(value);
          setError(null);
        }
      },
      (reason) => {
        if (active && !controller.signal.aborted) setError(errorMessage(reason));
      },
    ).finally(() => {
      if (active) setLoading(false);
    });
    return () => {
      active = false;
      controller.abort();
    };
  }, [api, loadAttempt, refreshKey, visible]);

  function refresh() {
    setLoading(true);
    setError(null);
    setLoadAttempt((attempt) => attempt + 1);
  }

  const source = useMemo(
    () => dashboard?.sources.find((item) => item.sourcePackage === selectedSource)
      ?? dashboard?.sources[0] ?? null,
    [dashboard, selectedSource],
  );
  const dailySteps = source?.daily.filter((day) => day.steps > 0) ?? [];
  const heartRateTrend = source?.heartRateTrend ?? [];
  const sleepSessions = source?.sleepSessions ?? [];
  const usedStages = [...new Set(sleepSessions.flatMap((session) =>
    session.stages.map((stage) => stage.stage)))];
  const totals = source?.totals;

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
          <Text style={s.headerTitle}>健康看板</Text>
          <Pressable
            accessibilityLabel="刷新健康看板"
            accessibilityRole="button"
            disabled={loading}
            onPress={refresh}
            style={s.refreshButton}
          >
            <Text style={s.refreshText}>{loading ? '读取中' : '刷新'}</Text>
          </Pressable>
        </View>

        {loading && dashboard === null ? (
          <View style={s.center}><ActivityIndicator color={colors.primary} /></View>
        ) : error !== null && (dashboard === null || dashboard.sources.length === 0) ? (
          <View style={s.center}>
            <Text style={s.hint}>{error}</Text>
            <Pressable onPress={refresh}>
              <Text style={s.retry}>重试</Text>
            </Pressable>
          </View>
        ) : source === null || totals === undefined ? (
          <ScrollView contentContainerStyle={s.body}>
            <Text style={s.hint}>近 30 天还没有保存到服务器的手环观测。</Text>
            {importPreview === null ? null : <LocalImportExtras bundle={importPreview} />}
          </ScrollView>
        ) : (
          <ScrollView
            contentContainerStyle={s.body}
            refreshControl={
              <RefreshControl
                onRefresh={refresh}
                refreshing={loading}
              />
            }
            showsVerticalScrollIndicator={false}
          >
            {error === null ? null : <Text style={s.error}>{error}</Text>}
            <Text style={s.source}>
              服务器已保存的近 30 天观测 · 历史记录，非实时测量
              {'\n'}最新观测 {formatDay(source.latestObservedAt)} {formatClock(source.latestObservedAt)}
              {source.device === null ? '' :
                ` · ${[source.device.manufacturer, source.device.model].filter(Boolean).join(' ')}`}
            </Text>

            {dashboard !== null && dashboard.sources.length > 1 ? (
              <View style={s.sourceTabs}>
                {dashboard.sources.map((item) => (
                  <Pressable
                    accessibilityLabel={`查看${SOURCE_LABELS[item.sourcePackage] ?? item.sourcePackage}数据`}
                    accessibilityRole="button"
                    accessibilityState={{ selected: item.sourcePackage === source.sourcePackage }}
                    key={item.sourcePackage}
                    onPress={() => setSelectedSource(item.sourcePackage)}
                    style={[s.sourceTab, item.sourcePackage === source.sourcePackage && s.sourceTabActive]}
                  >
                    <Text style={[
                      s.sourceTabText,
                      item.sourcePackage === source.sourcePackage && s.sourceTabTextActive,
                    ]}>
                      {SOURCE_LABELS[item.sourcePackage] ?? item.sourcePackage}
                    </Text>
                  </Pressable>
                ))}
              </View>
            ) : (
              <Text style={s.singleSource}>{SOURCE_LABELS[source.sourcePackage] ?? source.sourcePackage}</Text>
            )}

            <View style={s.cardRow}>
              <Card
                label="平均心率"
                value={totals.heartRateAverage === null ? '—' : `${totals.heartRateAverage} bpm`}
                sub={`${totals.heartRateSamples} 条样本`}
              />
              <Card
                label="心率范围"
                value={totals.heartRateMinimum === null || totals.heartRateMaximum === null
                  ? '—' : `${totals.heartRateMinimum}–${totals.heartRateMaximum}`}
                sub="最低 – 最高 bpm"
              />
            </View>
            <View style={s.cardRow}>
              <Card label="总步数" value={totals.steps.toLocaleString()} sub="近 30 天合计" />
              <Card
                label="总距离"
                value={`${(totals.distanceMeters / 1000).toFixed(1)} km`}
                sub="近 30 天合计"
              />
            </View>
            <View style={s.cardRow}>
              <Card
                label="最低血氧"
                value={totals.oxygenSaturationMinimum === null
                  ? '—' : `${totals.oxygenSaturationMinimum}%`}
                sub={`平均 ${totals.oxygenSaturationAverage ?? '—'}%，${totals.oxygenSaturationSamples} 条`}
              />
              <Card
                label="睡眠时长"
                value={formatDuration(totals.sleepMinutes)}
                sub={`${totals.sleepSessions} 次睡眠记录`}
              />
            </View>
            <View style={s.cardRow}>
              <Card
                label="最低静息心率"
                value={totals.restingHeartRateMinimum === null
                  ? '—' : `${totals.restingHeartRateMinimum} bpm`}
                sub={`${totals.restingHeartRateSamples} 条记录`}
              />
            </View>

            <Section title="每日步数" />
            {dailySteps.length >= 2 ? (
              <LineChart
                data={{
                  labels: dailySteps.map((day, index) =>
                    index % Math.ceil(dailySteps.length / 4) === 0 ? formatDay(day.day) : ''),
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
            ) : <Text style={s.hint}>有步数的日子不足两天</Text>}

            <Section title={`心率趋势（最近 ${heartRateTrend.length} 个采样点）`} />
            {heartRateTrend.length >= 2 ? (
              <LineChart
                data={{
                  labels: heartRateTrend.map((point, index) =>
                    index % Math.ceil(heartRateTrend.length / 4) === 0
                      ? formatClock(point.observedAt) : ''),
                  datasets: [{ data: heartRateTrend.map((point) => point.beatsPerMinute) }],
                }}
                width={W - spacing.md * 2}
                height={180}
                chartConfig={chartConfig}
                bezier
                fromZero={false}
                withDots={false}
                style={s.chart}
              />
            ) : <Text style={s.hint}>近 30 天没有足够的心率样本</Text>}

            <Section title="最近 7 次睡眠阶段" />
            {sleepSessions.length === 0 ? (
              <Text style={s.hint}>近 30 天没有睡眠阶段记录</Text>
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

            <Section title="数据说明" />
            <Text style={s.note}>
              看板按 Zepp 和 Gadgetbridge 分开统计，数据来自服务器保存的历史观测。
              压力、热量和厂商睡眠评分尚未上传，不能在这里恢复。
            </Text>
            {source.sourcePackage === 'nodomain.freeyourgadget.gadgetbridge' &&
              importPreview !== null ? <LocalImportExtras bundle={importPreview} /> : null}
          </ScrollView>
        )}
      </SafeAreaView>
    </Modal>
  );
}

function LocalImportExtras({ bundle }: { bundle: HealthBundle }) {
  const scoredSleep = bundle.sleep.filter((session) => session.score !== null).at(-1);
  return (
    <>
      <Section title="本次导入文件的附加指标" />
      <Text style={s.note}>
        以下数据只来自刚选取的 Gadgetbridge 文件，尚未保存到服务器；重启 App 后不会保留。
      </Text>
      <View style={s.cardRow}>
        <Card
          label="平均压力"
          value={bundle.totals.stressAvg === null ? '—' : String(bundle.totals.stressAvg)}
          sub={`${bundle.totals.stressCount} 条记录`}
        />
        <Card
          label="总热量"
          value={`${bundle.totals.totalCaloriesKcal.toFixed(1)} kcal`}
          sub="本次文件合计"
        />
      </View>
      {scoredSleep === undefined ? null : (
        <View style={s.cardRow}>
          <Card
            label="最近睡眠评分"
            value={String(scoredSleep.score)}
            sub={`${formatDay(scoredSleep.startTime)} 的厂商评分`}
          />
          <Card
            label="该晚深睡"
            value={scoredSleep.deepMinutes === null ? '—' : formatDuration(scoredSleep.deepMinutes)}
          />
        </View>
      )}
    </>
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

function SleepRow({ session }: { session: WearableDashboardSleepSession }) {
  const awakeMinutes = stageMinutes(session, 'awake') + stageMinutes(session, 'awake_in_bed');
  const asleepMinutes = stageMinutes(session, 'light') + stageMinutes(session, 'deep') +
    stageMinutes(session, 'rem') + stageMinutes(session, 'sleeping');
  return (
    <View style={s.sleepRow}>
      <View style={s.sleepHeader}>
        <Text style={s.sleepDate}>{formatDay(session.startTime)}</Text>
        <Text style={s.sleepMeta}>
          {formatClock(session.startTime)}–{formatClock(session.endTime)}　
          {formatDuration(asleepMinutes)}
          {awakeMinutes >= 1 ? `（清醒 ${Math.round(awakeMinutes)} 分）` : ''}
        </Text>
      </View>
      <View style={s.timeline}>
        {session.stages.map((slice) => {
          const minutes = Math.max(1, (Date.parse(slice.endTime) - Date.parse(slice.startTime)) / 60000);
          return (
            <View
              key={`${slice.startTime}-${slice.stage}`}
              style={{ flex: minutes, backgroundColor: STAGE_STYLE[slice.stage].color }}
            />
          );
        })}
      </View>
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
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  backButton: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', borderRadius: radii.pill },
  pressed: { opacity: 0.6 },
  headerTitle: { flex: 1, textAlign: 'center', color: colors.ink, fontFamily: typefaces.sansMedium, fontSize: 17 },
  refreshButton: { minWidth: 40, alignItems: 'center' },
  refreshText: { color: colors.primary, fontFamily: typefaces.sansMedium, fontSize: 13 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  hint: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 13, textAlign: 'center', lineHeight: 21 },
  retry: { color: colors.primary, fontFamily: typefaces.sansMedium, fontSize: 14, marginTop: spacing.md },
  error: { color: colors.danger, fontFamily: typefaces.sans, fontSize: 12, marginBottom: spacing.sm },
  body: { padding: spacing.md, paddingBottom: 40 },
  source: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 12, lineHeight: 20 },
  sourceTabs: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.md },
  sourceTab: { flex: 1, padding: spacing.sm, borderRadius: radii.md, backgroundColor: colors.paper, alignItems: 'center' },
  sourceTabActive: { backgroundColor: colors.primarySoft },
  sourceTabText: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 12 },
  sourceTabTextActive: { color: colors.primaryPressed, fontFamily: typefaces.sansMedium },
  singleSource: { color: colors.primaryPressed, fontFamily: typefaces.sansMedium, fontSize: 13, marginTop: spacing.md },
  section: { fontSize: 15, fontFamily: typefaces.sansMedium, color: colors.ink, marginTop: spacing.lg, marginBottom: spacing.sm },
  cardRow: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm },
  card: { flex: 1, backgroundColor: '#ffffff', borderRadius: radii.lg, padding: spacing.md },
  cardLabel: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 12 },
  cardValue: { fontSize: 21, fontFamily: typefaces.sansMedium, color: colors.ink, marginTop: 6 },
  cardSub: { color: colors.faint, fontFamily: typefaces.sans, fontSize: 11, marginTop: 2, lineHeight: 15 },
  chart: { borderRadius: radii.lg },
  legend: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md, marginBottom: spacing.sm },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  legendDot: { width: 10, height: 10, borderRadius: 3 },
  legendText: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 11 },
  sleepRow: { backgroundColor: '#ffffff', borderRadius: radii.lg, padding: spacing.md, marginBottom: spacing.sm },
  sleepHeader: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  sleepDate: { color: colors.ink, fontFamily: typefaces.sansMedium, fontSize: 13, minWidth: 40 },
  sleepMeta: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 11, flex: 1 },
  timeline: { flexDirection: 'row', height: 18, borderRadius: 5, overflow: 'hidden', marginTop: spacing.sm },
  note: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 12, lineHeight: 20 },
});
