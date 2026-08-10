import { useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import type {
  HealthFactType,
  HealthProfileCard,
  HealthProfileCardAnswer,
  PersonalProfileField,
  PersonalProfileUnit,
} from '../api/types';
import { colors, radii, spacing, typefaces } from '../ui/theme';
import {
  HealthFactEditor,
  PersonalValueEditor,
  healthFactTypeLabels,
  personalFieldLabels,
  type HealthFactValue,
} from './health-profile-inputs';

type Props = {
  cards: HealthProfileCard[];
  loading: boolean;
  actingCardId: string | null;
  error: string | null;
  onAnswer: (cardId: string, answer: HealthProfileCardAnswer) => void;
};

const personalFields = new Set<PersonalProfileField>(
  Object.keys(personalFieldLabels) as PersonalProfileField[],
);
const healthFactTypes = new Set<HealthFactType>(
  Object.keys(healthFactTypeLabels) as HealthFactType[],
);
const personalUnits = new Set<PersonalProfileUnit>([
  'years',
  'cm',
  'm',
  'kg',
  'jin',
  'lb',
]);

function personalInitialValue(card: HealthProfileCard) {
  const proposed = card.proposedValue;
  const raw = proposed?.source_value ?? proposed?.value;
  const unit = proposed?.source_unit ?? proposed?.canonical_unit;
  return {
    value:
      typeof raw === 'string' ||
      (typeof raw === 'number' && Number.isFinite(raw))
        ? String(raw)
        : '',
    unit:
      typeof unit === 'string' &&
      personalUnits.has(unit as PersonalProfileUnit)
        ? (unit as PersonalProfileUnit)
        : undefined,
  };
}

function healthFactInitialValue(
  card: HealthProfileCard,
): Partial<HealthFactValue> {
  const proposed = card.proposedValue;
  const assertion = proposed?.assertion;
  const temporalStatus = proposed?.temporal_status;
  return {
    statement:
      typeof proposed?.statement === 'string' ? proposed.statement : '',
    assertion:
      assertion === 'present' || assertion === 'absent' ? assertion : undefined,
    temporalStatus:
      temporalStatus === 'current' ||
      temporalStatus === 'past' ||
      temporalStatus === 'unknown'
        ? temporalStatus
        : undefined,
  };
}

export function HealthProfileCards({
  cards,
  loading,
  actingCardId,
  error,
  onAnswer,
}: Props) {
  const [expandedCardId, setExpandedCardId] = useState<string | null>(null);

  if (!loading && cards.length === 0 && error === null) return null;

  return (
    <View style={styles.container}>
      {loading && cards.length === 0 ? (
        <View style={styles.loadingRow}>
          <ActivityIndicator color={colors.primary} size="small" />
          <Text style={styles.loadingText}>正在读取健康档案确认项…</Text>
        </View>
      ) : null}

      {cards.map((card) => {
        const acting = actingCardId === card.id;
        const disabled = actingCardId !== null;
        const reject = card.options.find((option) => option.id === 'reject');
        const choices = card.options.filter((option) => option.id !== 'reject');
        const customOpen =
          expandedCardId === card.id ||
          (card.kind === 'clarification' &&
            choices.length === 0 &&
            card.allowCustomInput);
        const personalField = personalFields.has(
          card.fieldName as PersonalProfileField,
        )
          ? (card.fieldName as PersonalProfileField)
          : null;
        const factType = healthFactTypes.has(card.fieldName as HealthFactType)
          ? (card.fieldName as HealthFactType)
          : null;
        const validTarget =
          card.targetType === 'personal_profile'
            ? personalField !== null
            : factType !== null;
        const personalInitial = personalInitialValue(card);

        return (
          <View key={card.id} style={styles.card}>
            <View style={styles.cardHeader}>
              <Text style={styles.badge}>
                {card.kind === 'confirmation' ? '请确认' : '补充一下'}
              </Text>
              <Text style={styles.field}>
                {personalField === null
                  ? factType === null
                    ? '健康档案'
                    : healthFactTypeLabels[factType]
                  : personalFieldLabels[personalField]}
              </Text>
            </View>
            <Text selectable style={styles.question}>
              {card.question}
            </Text>

            {!validTarget ? (
              <Text selectable style={styles.errorText}>
                这条确认项暂时无法显示，请刷新后重试。
              </Text>
            ) : (
              <>
                {choices.length === 0 ? null : (
                  <View style={styles.optionGrid}>
                    {choices.map((option) => (
                      <Pressable
                        accessibilityLabel={`选择${option.label}`}
                        accessibilityRole="button"
                        disabled={disabled}
                        key={option.id}
                        onPress={() =>
                          onAnswer(card.id, { optionId: option.id })
                        }
                        style={({ pressed }) => [
                          styles.optionButton,
                          disabled && styles.disabledButton,
                          pressed && !disabled && styles.pressedButton,
                        ]}
                      >
                        {acting ? (
                          <ActivityIndicator color={colors.white} size="small" />
                        ) : (
                          <Text style={styles.optionButtonText}>
                            {option.label}
                          </Text>
                        )}
                      </Pressable>
                    ))}
                  </View>
                )}

                {card.allowCustomInput && !customOpen ? (
                  <Pressable
                    accessibilityLabel="填写其他健康档案内容"
                    accessibilityRole="button"
                    disabled={disabled}
                    onPress={() => setExpandedCardId(card.id)}
                    style={({ pressed }) => [
                      styles.customButton,
                      disabled && styles.disabledButton,
                      pressed && !disabled && styles.pressedButton,
                    ]}
                  >
                    <Text style={styles.customButtonText}>
                      {card.kind === 'confirmation' && choices.length === 1
                        ? '不是这个'
                        : '其他'}
                    </Text>
                  </Pressable>
                ) : null}

                {!customOpen ? null : card.targetType === 'personal_profile' &&
                  personalField !== null ? (
                  <PersonalValueEditor
                    disabled={disabled}
                    fieldName={personalField}
                    initialUnit={personalInitial.unit}
                    initialValue={personalInitial.value}
                    onCancel={
                      card.kind === 'clarification' && choices.length === 0
                        ? undefined
                        : () => setExpandedCardId(null)
                    }
                    onSubmit={(value) =>
                      onAnswer(card.id, {
                        customAnswer: {
                          targetType: 'personal_profile',
                          ...value,
                        },
                      })
                    }
                    placeholder={card.customInputPlaceholder}
                    submitLabel="确认写入"
                  />
                ) : factType !== null ? (
                  <HealthFactEditor
                    disabled={disabled}
                    fixedFactType={factType}
                    initialValue={healthFactInitialValue(card)}
                    onCancel={
                      card.kind === 'clarification' && choices.length === 0
                        ? undefined
                        : () => setExpandedCardId(null)
                    }
                    onSubmit={(value) =>
                      onAnswer(card.id, {
                        customAnswer: {
                          targetType: 'health_fact',
                          statement: value.statement,
                          assertion: value.assertion,
                          temporalStatus: value.temporalStatus,
                        },
                      })
                    }
                    placeholder={card.customInputPlaceholder}
                    submitLabel="确认写入"
                  />
                ) : null}
              </>
            )}

            {reject === undefined ? null : (
              <Pressable
                accessibilityLabel="暂不写入健康档案"
                accessibilityRole="button"
                disabled={disabled}
                onPress={() => onAnswer(card.id, { optionId: reject.id })}
                style={({ pressed }) => [
                  styles.rejectButton,
                  disabled && styles.disabledButton,
                  pressed && !disabled && styles.pressedButton,
                ]}
              >
                <Text style={styles.rejectButtonText}>{reject.label}</Text>
              </Pressable>
            )}
          </View>
        );
      })}

      {error === null ? null : (
        <Text selectable style={styles.errorText}>
          确认项加载失败：{error}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: spacing.sm, paddingTop: spacing.xs },
  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xs,
    paddingVertical: spacing.sm,
  },
  loadingText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  card: {
    gap: spacing.sm,
    padding: spacing.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: '#CDE3DD',
    borderRadius: radii.xl,
    borderCurve: 'continuous',
    backgroundColor: '#F3FBF8',
  },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  badge: {
    overflow: 'hidden',
    paddingHorizontal: spacing.xs,
    paddingVertical: 4,
    borderRadius: radii.pill,
    color: colors.primaryPressed,
    backgroundColor: '#DDF5EE',
    fontFamily: typefaces.sansMedium,
    fontSize: 10,
    letterSpacing: 0.4,
  },
  field: {
    color: colors.muted,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
  },
  question: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 16,
    lineHeight: 24,
    fontWeight: '600',
  },
  optionGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  optionButton: {
    minHeight: 40,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  optionButtonText: {
    color: colors.white,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },
  customButton: {
    alignSelf: 'flex-start',
    minHeight: 38,
    justifyContent: 'center',
    paddingHorizontal: spacing.sm,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: '#BFD8D2',
    borderRadius: radii.pill,
    backgroundColor: colors.paper,
  },
  customButtonText: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },
  rejectButton: { alignSelf: 'flex-start', paddingVertical: 5 },
  rejectButtonText: {
    color: colors.muted,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },
  disabledButton: { opacity: 0.5 },
  pressedButton: { opacity: 0.72 },
  errorText: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
  },
});
