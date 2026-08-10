import { useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import type {
  HealthFact,
  HealthFactType,
  PersonalProfileField,
  PersonalProfileUnit,
} from '../api/types';
import { colors, radii, spacing, typefaces } from '../ui/theme';

export const personalFieldLabels: Record<PersonalProfileField, string> = {
  sex: '性别',
  age_years: '年龄',
  height_cm: '身高',
  weight_kg: '体重',
  resident_area: '常驻地区',
  schedule_type: '日常作息',
  occupation: '职业',
};

export const healthFactTypeLabels: Record<HealthFactType, string> = {
  medical_history: '健康经历',
  allergy: '过敏情况',
  severe_hypoglycemia_history: '严重低血糖史',
  treatment: '治疗情况',
};

const personalFieldPlaceholders: Record<PersonalProfileField, string> = {
  sex: '例如：女',
  age_years: '输入年龄',
  height_cm: '输入身高',
  weight_kg: '输入体重',
  resident_area: '例如：上海',
  schedule_type: '例如：规律作息、夜班',
  occupation: '例如：设计师',
};

const unitOptions: Partial<
  Record<PersonalProfileField, { value: PersonalProfileUnit; label: string }[]>
> = {
  age_years: [{ value: 'years', label: '岁' }],
  height_cm: [
    { value: 'cm', label: '厘米' },
    { value: 'm', label: '米' },
  ],
  weight_kg: [
    { value: 'kg', label: '公斤' },
    { value: 'jin', label: '斤' },
  ],
};

export type PersonalValue = {
  value: string;
  unit?: PersonalProfileUnit;
};

export function PersonalValueEditor({
  disabled = false,
  fieldName,
  initialUnit,
  initialValue = '',
  onCancel,
  onSubmit,
  placeholder,
  submitLabel = '保存',
}: {
  disabled?: boolean;
  fieldName: PersonalProfileField;
  initialUnit?: PersonalProfileUnit;
  initialValue?: string;
  onCancel?: () => void;
  onSubmit: (value: PersonalValue) => void;
  placeholder?: string | null;
  submitLabel?: string;
}) {
  const units = unitOptions[fieldName] ?? [];
  const [value, setValue] = useState(initialValue);
  const [unit, setUnit] = useState<PersonalProfileUnit | undefined>(
    initialUnit ?? units[0]?.value,
  );

  const trimmed = value.trim();
  const numeric = units.length > 0;
  const invalidNumber =
    numeric &&
    (trimmed.length === 0 ||
      !Number.isFinite(Number(trimmed)) ||
      Number(trimmed) <= 0);
  const invalidAge =
    fieldName === 'age_years' &&
    trimmed.length > 0 &&
    !Number.isInteger(Number(trimmed));
  const canSubmit =
    !disabled && trimmed.length > 0 && !invalidNumber && !invalidAge;

  return (
    <View style={styles.editor}>
      <Text style={styles.editorLabel}>{personalFieldLabels[fieldName]}</Text>
      <View style={styles.valueRow}>
        <TextInput
          accessibilityLabel={`输入${personalFieldLabels[fieldName]}`}
          autoFocus
          editable={!disabled}
          keyboardType={numeric ? 'decimal-pad' : 'default'}
          maxLength={200}
          onChangeText={setValue}
          placeholder={placeholder ?? personalFieldPlaceholders[fieldName]}
          placeholderTextColor={colors.faint}
          selectionColor={colors.primary}
          style={styles.textInput}
          value={value}
        />
      </View>
      {units.length === 0 ? null : (
        <View accessibilityRole="radiogroup" style={styles.choiceRow}>
          {units.map((option) => {
            const selected = unit === option.value;
            return (
              <Pressable
                accessibilityLabel={`${personalFieldLabels[fieldName]}单位${option.label}`}
                accessibilityRole="radio"
                accessibilityState={{ checked: selected, disabled }}
                disabled={disabled}
                key={option.value}
                onPress={() => setUnit(option.value)}
                style={({ pressed }) => [
                  styles.choice,
                  selected && styles.choiceSelected,
                  pressed && !disabled && styles.pressed,
                ]}
              >
                <Text
                  style={[
                    styles.choiceText,
                    selected && styles.choiceTextSelected,
                  ]}
                >
                  {option.label}
                </Text>
              </Pressable>
            );
          })}
        </View>
      )}
      {(invalidNumber || invalidAge) && trimmed.length > 0 ? (
        <Text selectable style={styles.validationText}>
          {invalidAge ? '年龄请输入整数' : '请输入大于 0 的数字'}
        </Text>
      ) : null}
      <EditorActions
        disabled={!canSubmit}
        loading={disabled}
        onCancel={onCancel}
        onSubmit={() =>
          onSubmit({
            value: trimmed,
            ...(unit === undefined ? {} : { unit }),
          })
        }
        submitLabel={submitLabel}
      />
    </View>
  );
}

export type HealthFactValue = {
  factType: HealthFactType;
  statement: string;
  assertion: HealthFact['assertion'];
  temporalStatus: HealthFact['temporalStatus'];
};

export function HealthFactEditor({
  disabled = false,
  fixedFactType,
  initialValue,
  onCancel,
  onSubmit,
  placeholder,
  submitLabel = '保存',
}: {
  disabled?: boolean;
  fixedFactType?: HealthFactType;
  initialValue?: Partial<HealthFactValue>;
  onCancel?: () => void;
  onSubmit: (value: HealthFactValue) => void;
  placeholder?: string | null;
  submitLabel?: string;
}) {
  const [factType, setFactType] = useState<HealthFactType>(
    fixedFactType ?? initialValue?.factType ?? 'medical_history',
  );
  const [statement, setStatement] = useState(initialValue?.statement ?? '');
  const [assertion, setAssertion] = useState<HealthFact['assertion']>(
    initialValue?.assertion ?? 'present',
  );
  const [temporalStatus, setTemporalStatus] = useState<
    HealthFact['temporalStatus']
  >(initialValue?.temporalStatus ?? 'current');

  const trimmed = statement.trim();

  return (
    <View style={styles.editor}>
      <Text style={styles.editorLabel}>记录类型</Text>
      {fixedFactType === undefined ? (
        <View accessibilityRole="radiogroup" style={styles.choiceRow}>
          {(Object.keys(healthFactTypeLabels) as HealthFactType[]).map((value) => (
            <Choice
              disabled={disabled}
              key={value}
              label={healthFactTypeLabels[value]}
              onPress={() => setFactType(value)}
              selected={factType === value}
            />
          ))}
        </View>
      ) : (
        <View style={styles.fixedTypePill}>
          <Text style={styles.fixedTypeText}>
            {healthFactTypeLabels[fixedFactType]}
          </Text>
        </View>
      )}

      <Text style={styles.editorLabel}>具体情况</Text>
      <TextInput
        accessibilityLabel="输入健康情况"
        editable={!disabled}
        maxLength={500}
        multiline
        onChangeText={setStatement}
        placeholder={placeholder ?? '用一句话写清楚，例如：对青霉素过敏'}
        placeholderTextColor={colors.faint}
        selectionColor={colors.primary}
        style={[styles.textInput, styles.multilineInput]}
        textAlignVertical="top"
        value={statement}
      />

      <Text style={styles.editorLabel}>是否存在</Text>
      <View accessibilityRole="radiogroup" style={styles.choiceRow}>
        <Choice
          disabled={disabled}
          label="有"
          onPress={() => setAssertion('present')}
          selected={assertion === 'present'}
        />
        <Choice
          disabled={disabled}
          label="没有"
          onPress={() => setAssertion('absent')}
          selected={assertion === 'absent'}
        />
      </View>

      <Text style={styles.editorLabel}>时间</Text>
      <View accessibilityRole="radiogroup" style={styles.choiceRow}>
        {(
          [
            ['current', '目前'],
            ['past', '过去'],
            ['unknown', '不确定'],
          ] as const
        ).map(([value, label]) => (
          <Choice
            disabled={disabled}
            key={value}
            label={label}
            onPress={() => setTemporalStatus(value)}
            selected={temporalStatus === value}
          />
        ))}
      </View>

      <EditorActions
        disabled={disabled || trimmed.length === 0}
        loading={disabled}
        onCancel={onCancel}
        onSubmit={() =>
          onSubmit({ factType, statement: trimmed, assertion, temporalStatus })
        }
        submitLabel={submitLabel}
      />
    </View>
  );
}

function Choice({
  disabled,
  label,
  onPress,
  selected,
}: {
  disabled: boolean;
  label: string;
  onPress: () => void;
  selected: boolean;
}) {
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="radio"
      accessibilityState={{ checked: selected, disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.choice,
        selected && styles.choiceSelected,
        pressed && !disabled && styles.pressed,
      ]}
    >
      <Text
        style={[styles.choiceText, selected && styles.choiceTextSelected]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

function EditorActions({
  disabled,
  loading,
  onCancel,
  onSubmit,
  submitLabel,
}: {
  disabled: boolean;
  loading: boolean;
  onCancel?: () => void;
  onSubmit: () => void;
  submitLabel: string;
}) {
  return (
    <View style={styles.actions}>
      {onCancel === undefined ? null : (
        <Pressable
          accessibilityRole="button"
          disabled={loading}
          onPress={onCancel}
          style={({ pressed }) => [
            styles.cancelButton,
            pressed && !loading && styles.pressed,
          ]}
        >
          <Text style={styles.cancelText}>取消</Text>
        </Pressable>
      )}
      <Pressable
        accessibilityLabel={submitLabel}
        accessibilityRole="button"
        disabled={disabled}
        onPress={onSubmit}
        style={({ pressed }) => [
          styles.submitButton,
          disabled && styles.disabled,
          pressed && !disabled && styles.pressed,
        ]}
      >
        {loading ? (
          <ActivityIndicator color={colors.white} size="small" />
        ) : (
          <Text style={styles.submitText}>{submitLabel}</Text>
        )}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  editor: {
    gap: spacing.sm,
    padding: spacing.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    borderRadius: radii.lg,
    borderCurve: 'continuous',
    backgroundColor: colors.primarySoft,
  },
  editorLabel: {
    color: colors.muted,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
    fontWeight: '700',
  },
  valueRow: { flexDirection: 'row', gap: spacing.xs },
  textInput: {
    minHeight: 48,
    flex: 1,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: '#CFE1DD',
    borderRadius: radii.md,
    borderCurve: 'continuous',
    color: colors.ink,
    backgroundColor: colors.paper,
    fontFamily: typefaces.sans,
    fontSize: 16,
  },
  multilineInput: { minHeight: 94 },
  choiceRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  choice: {
    minHeight: 38,
    justifyContent: 'center',
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: '#C9D9D5',
    borderRadius: radii.pill,
    backgroundColor: colors.paper,
  },
  choiceSelected: {
    borderColor: colors.primary,
    backgroundColor: colors.primary,
  },
  choiceText: {
    color: colors.text,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },
  choiceTextSelected: { color: colors.white },
  fixedTypePill: {
    alignSelf: 'flex-start',
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.paper,
  },
  fixedTypeText: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
    fontSize: 13,
  },
  validationText: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  actions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: spacing.xs,
    paddingTop: spacing.xxs,
  },
  cancelButton: {
    minHeight: 40,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
  },
  cancelText: {
    color: colors.muted,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
  },
  submitButton: {
    minWidth: 92,
    minHeight: 40,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  submitText: {
    color: colors.white,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
  },
  disabled: { opacity: 0.5 },
  pressed: { opacity: 0.72 },
});
