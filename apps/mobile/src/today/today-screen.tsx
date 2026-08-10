import { LinearGradient } from 'expo-linear-gradient';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import type { ApiClient } from '../api/client';
import { errorMessage } from '../api/client';
import type { ChatMessage, HealthProfile, WearableLatest } from '../api/types';
import { BrandMark } from '../ui/brand-mark';
import { colors, radii, spacing, typefaces } from '../ui/theme';
import { DEMO_GLUCOSE_VALUES, DEMO_PLAN_ITEMS } from './demo-health';

export type TodayScreenProps = {
  api: Pick<ApiClient, 'getHealthProfile'>;
  latestAssistantMessage?: ChatMessage | null;
  onClose: () => void;
  onOpenCare: () => void;
  onOpenHealth: () => void;
  onStartConversation: (prompt: string) => void;
  username: string;
};

type CheckIn = 'steady' | 'uneasy' | 'worried';

const timeFormatter = new Intl.DateTimeFormat('zh-CN', {
  month: 'numeric',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function latestWearable(
  records: WearableLatest[],
  recordType: WearableLatest['recordType'],
) {
  return records.find((record) => record.recordType === recordType);
}

function sleepHours(record: WearableLatest | undefined): number | null {
  if (record?.recordType !== 'sleep') return null;
  const milliseconds = record.data.stages.reduce((total, stage) => {
    if (!['sleeping', 'light', 'deep', 'rem'].includes(stage.stage)) return total;
    return total + new Date(stage.endTime).getTime() - new Date(stage.startTime).getTime();
  }, 0);
  return milliseconds > 0 ? milliseconds / 3_600_000 : null;
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 11) return '早上好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

export function TodayScreen({
  api,
  latestAssistantMessage,
  onClose,
  onOpenCare,
  onOpenHealth,
  onStartConversation,
  username,
}: TodayScreenProps) {
  const [profile, setProfile] = useState<HealthProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [completedPlans, setCompletedPlans] = useState<string[]>(['breakfast']);
  const [checkIn, setCheckIn] = useState<CheckIn | null>(null);

  const reload = useCallback(
    async (refresh = false) => {
      if (refresh) setRefreshing(true);
      else setLoading(true);
      setLoadError(null);
      try {
        setProfile(await api.getHealthProfile());
      } catch (error) {
        setLoadError(errorMessage(error));
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [api],
  );

  useEffect(() => {
    const controller = new AbortController();
    void api.getHealthProfile(controller.signal).then(
      (result) => {
        if (!controller.signal.aborted) {
          setProfile(result);
          setLoading(false);
        }
      },
      (error) => {
        if (!controller.signal.aborted) {
          setLoadError(errorMessage(error));
          setLoading(false);
        }
      },
    );
    return () => controller.abort();
  }, [api]);

  const wearable = useMemo(() => profile?.wearableLatest ?? [], [profile]);
  const heartRateRecord = latestWearable(wearable, 'heart_rate');
  const restingHeartRateRecord = latestWearable(wearable, 'resting_heart_rate');
  const stepsRecord = latestWearable(wearable, 'steps');
  const oxygenRecord = latestWearable(wearable, 'oxygen_saturation');
  const sleepRecord = latestWearable(wearable, 'sleep');

  const heartRate =
    heartRateRecord?.recordType === 'heart_rate'
      ? heartRateRecord.data.samples.at(-1)?.beatsPerMinute ?? null
      : restingHeartRateRecord?.recordType === 'resting_heart_rate'
        ? restingHeartRateRecord.data.beatsPerMinute
        : null;
  const steps =
    stepsRecord?.recordType === 'steps' ? Math.round(stepsRecord.data.count) : null;
  const oxygen =
    oxygenRecord?.recordType === 'oxygen_saturation'
      ? Math.round(oxygenRecord.data.percentage)
      : null;
  const sleep = sleepHours(sleepRecord);
  const latestObservedAt = useMemo(() => {
    const timestamps = wearable.map((record) => new Date(record.observedAt).getTime());
    const timestamp = timestamps.length > 0 ? Math.max(...timestamps) : null;
    return timestamp === null ? null : timeFormatter.format(new Date(timestamp));
  }, [wearable]);

  const latestCompanionCopy = latestAssistantMessage?.content.trim();
  const displayName = username.includes('@') ? username.split('@')[0] : username;

  function togglePlan(id: string) {
    setCompletedPlans((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id],
    );
  }

  return (
    <View style={styles.screen}>
      <SafeAreaView edges={['top']} style={styles.safeHeader}>
        <View style={styles.header}>
          <Pressable
            accessibilityLabel="关闭今日"
            accessibilityRole="button"
            onPress={onClose}
            style={({ pressed }) => [styles.closeButton, pressed && styles.pressed]}
          >
            <Text accessibilityElementsHidden style={styles.closeGlyph}>‹</Text>
          </Pressable>
          <View style={styles.headerTitleRow}>
            <BrandMark size={30} />
            <Text style={styles.headerTitle}>今日</Text>
          </View>
          <Pressable
            accessibilityLabel="打开主动关怀设置"
            accessibilityRole="button"
            onPress={onOpenCare}
            style={({ pressed }) => [styles.careButton, pressed && styles.pressed]}
          >
            <View style={styles.careDot} />
            <Text style={styles.careButtonText}>关怀</Text>
          </Pressable>
        </View>
      </SafeAreaView>

      <ScrollView
        contentContainerStyle={styles.content}
        contentInsetAdjustmentBehavior="automatic"
        refreshControl={
          <RefreshControl
            colors={[colors.coral]}
            onRefresh={() => void reload(true)}
            refreshing={refreshing}
            tintColor={colors.coral}
          />
        }
      >
        <View style={styles.greetingRow}>
          <View style={styles.greetingCopy}>
            <Text style={styles.eyebrow}>你的日常健康陪伴</Text>
            <Text style={styles.greeting}>{greeting()}，{displayName}</Text>
            <Text style={styles.greetingHint}>不用把每一天都过成满分。</Text>
          </View>
          <View style={styles.dateStamp}>
            <Text style={styles.dateNumber}>{new Date().getDate()}</Text>
            <Text style={styles.dateMonth}>
              {new Intl.DateTimeFormat('zh-CN', { month: 'short' }).format(new Date())}
            </Text>
          </View>
        </View>

        <LinearGradient
          colors={['#214F43', '#183A31']}
          end={{ x: 1, y: 1 }}
          start={{ x: 0, y: 0 }}
          style={styles.glucoseCard}
        >
          <View style={styles.cardHeading}>
            <View>
              <Text style={styles.darkCardEyebrow}>血糖趋势</Text>
              <Text style={styles.darkCardTitle}>平稳</Text>
            </View>
            <View style={styles.demoBadge}>
              <Text style={styles.demoBadgeText}>演示数据</Text>
            </View>
          </View>
          <View style={styles.glucoseReading}>
            <Text style={styles.glucoseValue}>6.8</Text>
            <View style={styles.glucoseUnitBlock}>
              <Text style={styles.glucoseArrow}>→</Text>
              <Text style={styles.glucoseUnit}>mmol/L</Text>
            </View>
          </View>
          <View
            accessibilityLabel="演示血糖曲线，当前 6.8 mmol/L，趋势平稳"
            style={styles.chart}
          >
            {DEMO_GLUCOSE_VALUES.map((value, index) => (
              <View
                key={`${index}-${value}`}
                style={[
                  styles.chartBar,
                  {
                    height: 8 + ((value - 4.5) / 4.5) * 54,
                    opacity: 0.38 + index / 42,
                  },
                ]}
              />
            ))}
          </View>
          <View style={styles.chartLabels}>
            <Text style={styles.chartLabel}>00:00</Text>
            <Text style={styles.chartLabel}>12:00</Text>
            <Text style={styles.chartLabel}>现在</Text>
          </View>
          <View style={styles.demoNotice}>
            <View style={styles.demoNoticeDot} />
            <Text style={styles.demoNoticeText}>
              这条曲线用于展示界面，不来自设备，也不用于医疗判断。
            </Text>
          </View>
        </LinearGradient>

        <View style={styles.sectionHeading}>
          <View>
            <Text style={styles.sectionEyebrow}>ZEPP · HEALTH CONNECT</Text>
            <Text style={styles.sectionTitle}>最近的手环记录</Text>
          </View>
          <Pressable
            accessibilityLabel="查看全部健康记录"
            accessibilityRole="button"
            onPress={onOpenHealth}
            style={({ pressed }) => pressed && styles.pressed}
          >
            <Text style={styles.sectionLink}>查看全部</Text>
          </Pressable>
        </View>

        {loading && profile === null ? (
          <View style={styles.loadingCard}>
            <ActivityIndicator color={colors.primary} />
            <Text style={styles.loadingText}>正在读取真实健康记录</Text>
          </View>
        ) : loadError !== null && profile === null ? (
          <Pressable
            accessibilityLabel="重试读取健康记录"
            accessibilityRole="button"
            onPress={() => void reload()}
            style={styles.errorCard}
          >
            <Text selectable style={styles.errorTitle}>健康记录暂时没读到</Text>
            <Text selectable style={styles.errorText}>{loadError}</Text>
            <Text style={styles.errorAction}>点这里重试</Text>
          </Pressable>
        ) : (
          <>
            <View style={styles.metricsGrid}>
              <MetricCard
                accent="coral"
                label="最近心率"
                suffix="次/分"
                value={heartRate === null ? '—' : String(Math.round(heartRate))}
              />
              <MetricCard
                accent="green"
                label="睡眠记录"
                suffix="小时"
                value={sleep === null ? '—' : sleep.toFixed(1)}
              />
              <MetricCard
                accent="sand"
                label="单条步数"
                suffix="步"
                value={steps === null ? '—' : steps.toLocaleString('zh-CN')}
              />
              <MetricCard
                accent="blue"
                label="最近血氧"
                suffix="%"
                value={oxygen === null ? '—' : String(oxygen)}
              />
            </View>
            <Text style={styles.dataFootnote}>
              {latestObservedAt === null
                ? '还没有从手环同步到记录。这里不会用模拟值补齐。'
                : `服务器历史记录 · 最近观测 ${latestObservedAt} · 不是实时测量`}
            </Text>
          </>
        )}

        <View style={styles.checkInCard}>
          <View style={styles.checkInTop}>
            <View style={styles.checkInIcon}><Text style={styles.checkInIconText}>心</Text></View>
            <View style={styles.checkInCopy}>
              <Text style={styles.cardEyebrow}>一秒钟签到 · 体验功能</Text>
              <Text style={styles.cardTitle}>想到低血糖时，你现在感觉怎样？</Text>
            </View>
          </View>
          <View style={styles.checkInChoices}>
            {([
              ['steady', '还算平静'],
              ['uneasy', '有点担心'],
              ['worried', '很担心'],
            ] as const).map(([value, label]) => (
              <Pressable
                accessibilityLabel={label}
                accessibilityRole="button"
                key={value}
                onPress={() => setCheckIn(value)}
                style={({ pressed }) => [
                  styles.checkInChoice,
                  checkIn === value && styles.checkInChoiceSelected,
                  pressed && styles.pressed,
                ]}
              >
                <Text style={[
                  styles.checkInChoiceText,
                  checkIn === value && styles.checkInChoiceTextSelected,
                ]}>{label}</Text>
              </Pressable>
            ))}
          </View>
          {checkIn === null ? (
            <Text style={styles.localOnly}>本页选择仅作交互演示，不会写入健康档案。</Text>
          ) : (
            <Pressable
              accessibilityLabel="带着这个感受聊一聊"
              accessibilityRole="button"
              onPress={() => onStartConversation(
                checkIn === 'steady'
                  ? '我现在想到低血糖还算平静，想简单聊聊。'
                  : checkIn === 'uneasy'
                    ? '我现在想到低血糖有点担心，陪我梳理一下好吗？'
                    : '我现在很担心低血糖，想和你聊聊。',
              )}
              style={({ pressed }) => [styles.talkButton, pressed && styles.talkButtonPressed]}
            >
              <Text style={styles.talkButtonText}>带着这个感受聊一聊</Text>
              <Text style={styles.talkButtonArrow}>→</Text>
            </Pressable>
          )}
        </View>

        <View style={styles.sectionHeading}>
          <View>
            <Text style={styles.sectionEyebrow}>今天的小计划 · 演示</Text>
            <Text style={styles.sectionTitle}>做一点，就算一点</Text>
          </View>
          <Text style={styles.planCount}>{completedPlans.length}/{DEMO_PLAN_ITEMS.length}</Text>
        </View>
        <View style={styles.planCard}>
          {DEMO_PLAN_ITEMS.map((item, index) => {
            const completed = completedPlans.includes(item.id);
            return (
              <Pressable
                accessibilityLabel={item.label}
                accessibilityRole="checkbox"
                accessibilityState={{ checked: completed }}
                key={item.id}
                onPress={() => togglePlan(item.id)}
                style={({ pressed }) => [
                  styles.planRow,
                  index > 0 && styles.planRowBorder,
                  pressed && styles.planRowPressed,
                ]}
              >
                <View style={[styles.planCheck, completed && styles.planCheckDone]}>
                  <Text style={styles.planCheckText}>{completed ? '✓' : ''}</Text>
                </View>
                <View style={styles.planCopy}>
                  <Text style={[styles.planLabel, completed && styles.planLabelDone]}>{item.label}</Text>
                  <Text style={styles.planMeta}>{item.meta}</Text>
                </View>
              </Pressable>
            );
          })}
        </View>

        <LinearGradient
          colors={['#F1D9D0', '#F6E9DF']}
          end={{ x: 1, y: 1 }}
          start={{ x: 0, y: 0 }}
          style={styles.companionCard}
        >
          <View style={styles.companionHeading}>
            <BrandMark size={36} />
            <View style={styles.companionHeadingCopy}>
              <Text style={styles.companionEyebrow}>安糖留给你的话</Text>
              <Text style={styles.companionTime}>
                {latestAssistantMessage === undefined || latestAssistantMessage === null
                  ? '从一段真实对话开始'
                  : timeFormatter.format(new Date(latestAssistantMessage.createdAt))}
              </Text>
            </View>
          </View>
          <Text numberOfLines={4} style={styles.companionText}>
            {latestCompanionCopy || '我会记住我们聊过的重要事情，也会在你愿意的时候陪你看看最近的状态。'}
          </Text>
          <Pressable
            accessibilityRole="button"
            onPress={() => onStartConversation('陪我看看今天的状态吧。')}
            style={({ pressed }) => [styles.companionButton, pressed && styles.pressed]}
          >
            <Text style={styles.companionButtonText}>回到对话</Text>
            <Text style={styles.companionButtonArrow}>→</Text>
          </Pressable>
        </LinearGradient>
      </ScrollView>
    </View>
  );
}

function MetricCard({
  accent,
  label,
  suffix,
  value,
}: {
  accent: 'blue' | 'coral' | 'green' | 'sand';
  label: string;
  suffix: string;
  value: string;
}) {
  return (
    <View style={styles.metricCard}>
      <View style={[styles.metricAccent, styles[`metricAccent${accent}`]]} />
      <Text style={styles.metricLabel}>{label}</Text>
      <View style={styles.metricValueRow}>
        <Text style={styles.metricValue}>{value}</Text>
        <Text style={styles.metricSuffix}>{suffix}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  safeHeader: { backgroundColor: colors.paper },
  header: {
    minHeight: 62,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  closeButton: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.md,
    backgroundColor: colors.paperMuted,
  },
  closeGlyph: { marginTop: -3, color: colors.ink, fontFamily: typefaces.serif, fontSize: 34 },
  headerTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  headerTitle: { color: colors.ink, fontFamily: typefaces.serif, fontSize: 19, fontWeight: '700' },
  careButton: {
    height: 38,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: spacing.sm,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    borderRadius: radii.pill,
  },
  careDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.coral },
  careButtonText: { color: colors.text, fontFamily: typefaces.sansMedium, fontSize: 11 },
  content: { paddingHorizontal: spacing.md, paddingTop: spacing.xl, paddingBottom: 52 },
  greetingRow: { flexDirection: 'row', alignItems: 'flex-end', gap: spacing.md, marginBottom: spacing.xl },
  greetingCopy: { flex: 1 },
  eyebrow: { color: colors.coralPressed, fontFamily: typefaces.sansMedium, fontSize: 10, letterSpacing: 1.3 },
  greeting: { marginTop: 5, color: colors.ink, fontFamily: typefaces.serif, fontSize: 29, fontWeight: '700' },
  greetingHint: { marginTop: 5, color: colors.muted, fontFamily: typefaces.sans, fontSize: 13 },
  dateStamp: {
    minWidth: 58,
    alignItems: 'center',
    paddingHorizontal: spacing.sm,
    paddingVertical: 9,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    borderRadius: radii.lg,
    backgroundColor: colors.paper,
  },
  dateNumber: { color: colors.ink, fontFamily: typefaces.serif, fontSize: 24, fontWeight: '700' },
  dateMonth: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 10 },
  glucoseCard: {
    overflow: 'hidden',
    padding: spacing.lg,
    borderRadius: radii.xl,
    borderCurve: 'continuous',
    boxShadow: '0 12px 28px rgba(24, 58, 49, 0.18)',
  },
  cardHeading: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between' },
  darkCardEyebrow: { color: '#BFD6CC', fontFamily: typefaces.sansMedium, fontSize: 10, letterSpacing: 1.2 },
  darkCardTitle: { marginTop: 3, color: colors.paper, fontFamily: typefaces.serif, fontSize: 19, fontWeight: '700' },
  demoBadge: { paddingHorizontal: 10, paddingVertical: 6, borderRadius: radii.pill, backgroundColor: 'rgba(255,252,246,0.13)' },
  demoBadgeText: { color: colors.paper, fontFamily: typefaces.sansMedium, fontSize: 10 },
  glucoseReading: { flexDirection: 'row', alignItems: 'flex-end', gap: spacing.sm, marginTop: spacing.md },
  glucoseValue: { color: colors.paper, fontFamily: typefaces.serif, fontSize: 51, fontWeight: '700', lineHeight: 56, fontVariant: ['tabular-nums'] },
  glucoseUnitBlock: { paddingBottom: 6 },
  glucoseArrow: { color: '#9CC7B5', fontFamily: typefaces.sansMedium, fontSize: 20 },
  glucoseUnit: { color: '#BFD6CC', fontFamily: typefaces.sans, fontSize: 10 },
  chart: { height: 72, flexDirection: 'row', alignItems: 'flex-end', gap: 3, marginTop: spacing.md, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: 'rgba(255,255,255,0.22)' },
  chartBar: { flex: 1, minHeight: 4, borderTopLeftRadius: 3, borderTopRightRadius: 3, backgroundColor: '#F1A38E' },
  chartLabels: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 5 },
  chartLabel: { color: '#AFC5BC', fontFamily: typefaces.sans, fontSize: 9, fontVariant: ['tabular-nums'] },
  demoNotice: { flexDirection: 'row', alignItems: 'flex-start', gap: 7, marginTop: spacing.md, paddingTop: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: 'rgba(255,255,255,0.13)' },
  demoNoticeDot: { width: 6, height: 6, marginTop: 5, borderRadius: 3, backgroundColor: colors.coral },
  demoNoticeText: { flex: 1, color: '#C7D8D1', fontFamily: typefaces.sans, fontSize: 10, lineHeight: 15 },
  sectionHeading: { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', gap: spacing.sm, marginTop: spacing.xxl, marginBottom: spacing.sm },
  sectionEyebrow: { color: colors.coralPressed, fontFamily: typefaces.sansMedium, fontSize: 9, letterSpacing: 1.1 },
  sectionTitle: { marginTop: 4, color: colors.ink, fontFamily: typefaces.serif, fontSize: 21, fontWeight: '700' },
  sectionLink: { paddingVertical: 5, color: colors.primary, fontFamily: typefaces.sansMedium, fontSize: 11 },
  loadingCard: { minHeight: 120, alignItems: 'center', justifyContent: 'center', gap: spacing.sm, borderRadius: radii.lg, backgroundColor: colors.paper },
  loadingText: { color: colors.muted, fontFamily: typefaces.sans, fontSize: 12 },
  errorCard: { gap: 5, padding: spacing.md, borderWidth: StyleSheet.hairlineWidth, borderColor: '#E1B5A9', borderRadius: radii.lg, backgroundColor: colors.dangerSoft },
  errorTitle: { color: colors.danger, fontFamily: typefaces.sansMedium, fontSize: 13 },
  errorText: { color: colors.text, fontFamily: typefaces.sans, fontSize: 11, lineHeight: 16 },
  errorAction: { marginTop: 3, color: colors.danger, fontFamily: typefaces.sansMedium, fontSize: 11 },
  metricsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  metricCard: { position: 'relative', overflow: 'hidden', width: '48.7%', minHeight: 112, padding: spacing.md, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line, borderRadius: radii.lg, backgroundColor: colors.paper },
  metricAccent: { position: 'absolute', top: 0, right: 0, width: 44, height: 5, borderBottomLeftRadius: 5 },
  metricAccentcoral: { backgroundColor: colors.coral },
  metricAccentgreen: { backgroundColor: colors.primary },
  metricAccentsand: { backgroundColor: '#BE9B63' },
  metricAccentblue: { backgroundColor: '#668D98' },
  metricLabel: { color: colors.muted, fontFamily: typefaces.sansMedium, fontSize: 11 },
  metricValueRow: { flex: 1, flexDirection: 'row', alignItems: 'flex-end', gap: 5, marginTop: spacing.sm },
  metricValue: { color: colors.ink, fontFamily: typefaces.serif, fontSize: 27, fontWeight: '700', fontVariant: ['tabular-nums'] },
  metricSuffix: { paddingBottom: 4, color: colors.muted, fontFamily: typefaces.sans, fontSize: 9 },
  dataFootnote: { marginTop: spacing.xs, color: colors.faint, fontFamily: typefaces.sans, fontSize: 9, lineHeight: 14 },
  checkInCard: { marginTop: spacing.xxl, padding: spacing.lg, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line, borderRadius: radii.xl, backgroundColor: colors.paper },
  checkInTop: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  checkInIcon: { width: 38, height: 38, alignItems: 'center', justifyContent: 'center', borderRadius: 13, backgroundColor: colors.coralSoft },
  checkInIconText: { color: colors.coralPressed, fontFamily: typefaces.serif, fontSize: 16, fontWeight: '700' },
  checkInCopy: { flex: 1 },
  cardEyebrow: { color: colors.coralPressed, fontFamily: typefaces.sansMedium, fontSize: 9, letterSpacing: 1 },
  cardTitle: { marginTop: 4, color: colors.ink, fontFamily: typefaces.serif, fontSize: 18, fontWeight: '700', lineHeight: 25 },
  checkInChoices: { flexDirection: 'row', gap: 6, marginTop: spacing.md },
  checkInChoice: { flex: 1, alignItems: 'center', paddingHorizontal: 4, paddingVertical: 10, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line, borderRadius: radii.md, backgroundColor: colors.background },
  checkInChoiceSelected: { borderColor: colors.ink, backgroundColor: colors.ink },
  checkInChoiceText: { color: colors.text, fontFamily: typefaces.sansMedium, fontSize: 10 },
  checkInChoiceTextSelected: { color: colors.paper },
  localOnly: { marginTop: spacing.sm, color: colors.faint, fontFamily: typefaces.sans, fontSize: 9, lineHeight: 14 },
  talkButton: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: spacing.sm, paddingHorizontal: spacing.md, paddingVertical: 11, borderRadius: radii.md, backgroundColor: colors.coralSoft },
  talkButtonPressed: { backgroundColor: '#EBCBC1' },
  talkButtonText: { color: colors.coralPressed, fontFamily: typefaces.sansMedium, fontSize: 11 },
  talkButtonArrow: { color: colors.coralPressed, fontFamily: typefaces.serif, fontSize: 17 },
  planCount: { color: colors.primary, fontFamily: typefaces.serif, fontSize: 19, fontWeight: '700' },
  planCard: { overflow: 'hidden', borderWidth: StyleSheet.hairlineWidth, borderColor: colors.line, borderRadius: radii.xl, backgroundColor: colors.paper },
  planRow: { minHeight: 69, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  planRowBorder: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  planRowPressed: { backgroundColor: colors.primarySoft },
  planCheck: { width: 25, height: 25, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: colors.line, borderRadius: 9, backgroundColor: colors.background },
  planCheckDone: { borderColor: colors.primary, backgroundColor: colors.primary },
  planCheckText: { color: colors.paper, fontFamily: typefaces.sansMedium, fontSize: 13 },
  planCopy: { flex: 1, gap: 3 },
  planLabel: { color: colors.text, fontFamily: typefaces.sansMedium, fontSize: 13 },
  planLabelDone: { color: colors.muted, textDecorationLine: 'line-through' },
  planMeta: { color: colors.faint, fontFamily: typefaces.sans, fontSize: 10 },
  companionCard: { marginTop: spacing.xxl, padding: spacing.lg, borderRadius: radii.xl, borderCurve: 'continuous' },
  companionHeading: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  companionHeadingCopy: { flex: 1 },
  companionEyebrow: { color: colors.coralPressed, fontFamily: typefaces.sansMedium, fontSize: 10, letterSpacing: 1 },
  companionTime: { marginTop: 2, color: colors.muted, fontFamily: typefaces.sans, fontSize: 9 },
  companionText: { marginTop: spacing.md, color: colors.ink, fontFamily: typefaces.serif, fontSize: 17, lineHeight: 26 },
  companionButton: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: spacing.md, paddingTop: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: 'rgba(191,99,79,0.28)' },
  companionButtonText: { color: colors.coralPressed, fontFamily: typefaces.sansMedium, fontSize: 11 },
  companionButtonArrow: { color: colors.coralPressed, fontFamily: typefaces.serif, fontSize: 18 },
  pressed: { opacity: 0.62 },
});
