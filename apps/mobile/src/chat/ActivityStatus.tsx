import { LinearGradient } from 'expo-linear-gradient';
import { useEffect, useMemo, useState } from 'react';
import {
  AccessibilityInfo,
  Animated,
  Easing,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import type { AgentActivityPhase } from '../api/types';
import { colors, radii, spacing, typefaces } from '../ui/theme';

const labels: Record<AgentActivityPhase, string> = {
  thinking: '正在思考',
  searching: '正在搜索相关资料',
  reading: '正在阅读来源',
  organizing: '正在整理回答',
};

type Props = {
  phase: AgentActivityPhase;
};

/** 只展示应用定义的安全短句，不渲染模型推理或工具参数。 */
export function ActivityStatus({ phase }: Props) {
  const [progress] = useState(() => new Animated.Value(0));
  const [reduceMotion, setReduceMotion] = useState(false);
  const label = labels[phase];
  const translateX = useMemo(
    () =>
      progress.interpolate({
        inputRange: [0, 1],
        outputRange: [-100, 340],
      }),
    [progress],
  );

  useEffect(() => {
    let active = true;
    void AccessibilityInfo.isReduceMotionEnabled().then((enabled) => {
      if (active) setReduceMotion(enabled);
    });
    const subscription = AccessibilityInfo.addEventListener(
      'reduceMotionChanged',
      setReduceMotion,
    );
    return () => {
      active = false;
      subscription.remove();
    };
  }, []);

  useEffect(() => {
    if (reduceMotion) {
      progress.stopAnimation();
      progress.setValue(0.5);
      return;
    }

    progress.setValue(0);
    const animation = Animated.loop(
      Animated.timing(progress, {
        toValue: 1,
        duration: 1600,
        easing: Easing.linear,
        useNativeDriver: true,
      }),
    );
    animation.start();
    return () => animation.stop();
  }, [progress, reduceMotion]);

  return (
    <View
      accessibilityLabel={label}
      accessibilityLiveRegion="polite"
      accessible
      style={styles.container}
    >
      <Animated.View
        pointerEvents="none"
        style={[
          styles.shimmer,
          {
            transform: [
              {
                translateX,
              },
            ],
          },
        ]}
      >
        <LinearGradient
          colors={[
            'rgba(255,252,246,0)',
            colors.paper,
            'rgba(255,252,246,0)',
          ]}
          end={{ x: 1, y: 0 }}
          start={{ x: 0, y: 0 }}
          style={StyleSheet.absoluteFill}
        />
      </Animated.View>
      <View style={styles.dot} />
      <Text style={styles.label}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    minHeight: 34,
    maxWidth: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    overflow: 'hidden',
    gap: spacing.xs,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    borderRadius: radii.pill,
    backgroundColor: colors.paperMuted,
  },
  shimmer: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: -100,
    width: 100,
    opacity: 0.72,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.coral,
  },
  label: {
    color: colors.muted,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },
});
