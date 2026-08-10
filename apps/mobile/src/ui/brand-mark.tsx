import { StyleSheet, View } from 'react-native';

import { colors } from './theme';

export type BrandMarkProps = {
  size?: number;
};

/**
 * A quiet conversation bubble assembled from native views.
 *
 * The short pulse line suggests a living, attentive conversation without
 * presenting the product as a real-time medical monitor.
 */
export function BrandMark({ size = 52 }: BrandMarkProps) {
  const unit = size / 52;

  return (
    <View
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      style={[
        styles.frame,
        {
          width: size,
          height: size,
        },
      ]}
    >
      <View
        style={[
          styles.tail,
          {
            width: 13 * unit,
            height: 13 * unit,
            left: 10 * unit,
            top: 34 * unit,
            borderRadius: 3 * unit,
          },
        ]}
      />
      <View
        style={[
          styles.bubble,
          {
            width: 48 * unit,
            height: 43 * unit,
            left: 2 * unit,
            top: 2 * unit,
            borderRadius: 16 * unit,
          },
        ]}
      >
        <View
          style={[
            styles.pulse,
            {
              width: 9 * unit,
              height: 2 * unit,
              left: 8 * unit,
              top: 22 * unit,
              borderRadius: unit,
            },
          ]}
        />
        <View
          style={[
            styles.pulse,
            {
              width: 10 * unit,
              height: 2 * unit,
              left: 15 * unit,
              top: 18 * unit,
              borderRadius: unit,
              transform: [{ rotate: '-48deg' }],
            },
          ]}
        />
        <View
          style={[
            styles.pulse,
            {
              width: 12 * unit,
              height: 2 * unit,
              left: 22 * unit,
              top: 18 * unit,
              borderRadius: unit,
              transform: [{ rotate: '55deg' }],
            },
          ]}
        />
        <View
          style={[
            styles.pulse,
            {
              width: 7 * unit,
              height: 2 * unit,
              left: 31 * unit,
              top: 22 * unit,
              borderRadius: unit,
            },
          ]}
        />
        <View
          style={[
            styles.coralDot,
            {
              width: 6 * unit,
              height: 6 * unit,
              left: 38 * unit,
              top: 20 * unit,
              borderRadius: 3 * unit,
            },
          ]}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  frame: {
    position: 'relative',
  },
  tail: {
    position: 'absolute',
    backgroundColor: colors.ink,
    transform: [{ rotate: '45deg' }],
  },
  bubble: {
    position: 'absolute',
    overflow: 'hidden',
    backgroundColor: colors.ink,
    borderCurve: 'continuous',
  },
  pulse: {
    position: 'absolute',
    backgroundColor: colors.paper,
  },
  coralDot: {
    position: 'absolute',
    backgroundColor: colors.coral,
  },
});
