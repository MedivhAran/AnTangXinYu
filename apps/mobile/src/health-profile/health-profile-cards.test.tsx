import { act, create } from 'react-test-renderer';

import type { HealthProfileCard } from '../api/types';
import { HealthProfileCards } from './health-profile-cards';

const baseCard: HealthProfileCard = {
  id: '019b3333-3333-7333-8333-333333333333',
  kind: 'confirmation',
  targetType: 'personal_profile',
  fieldName: 'height_cm',
  operation: 'set',
  question: '你是说你的身高是 169 cm 吗？',
  proposedValue: { value: 169, canonical_unit: 'cm' },
  options: [
    { id: 'accept', label: '是，确认写入' },
    { id: 'reject', label: '暂不写入' },
  ],
  allowCustomInput: true,
  customInputPlaceholder: '例如：1.68 米',
  createdAt: '2026-07-14T10:00:00Z',
};

function renderCard(card: HealthProfileCard) {
  const onAnswer = jest.fn();
  let tree: ReturnType<typeof create>;
  act(() => {
    tree = create(
      <HealthProfileCards
        actingCardId={null}
        cards={[card]}
        error={null}
        loading={false}
        onAnswer={onAnswer}
      />,
    );
  });
  return { tree: tree!, onAnswer };
}

describe('HealthProfileCards', () => {
  test('confirms an unambiguous height or opens a field-aware correction', () => {
    const { tree, onAnswer } = renderCard(baseCard);

    expect(
      tree.root.findByProps({ children: '你是说你的身高是 169 cm 吗？' }),
    ).toBeTruthy();
    act(() =>
      tree.root.findByProps({ accessibilityLabel: '选择是，确认写入' }).props
        .onPress(),
    );
    expect(onAnswer).toHaveBeenCalledWith(baseCard.id, {
      optionId: 'accept',
    });

    expect(tree.root.findByProps({ children: '不是这个' })).toBeTruthy();
    act(() =>
      tree.root.findByProps({ accessibilityLabel: '填写其他健康档案内容' })
        .props.onPress(),
    );
    expect(tree.root.findByProps({ accessibilityLabel: '输入身高' })).toBeTruthy();
    expect(
      tree.root.findByProps({ accessibilityLabel: '身高单位厘米' }),
    ).toBeTruthy();
    act(() => tree.unmount());
  });

  test('renders kilogram and jin choices and submits a custom weight inline', () => {
    const { tree, onAnswer } = renderCard({
      ...baseCard,
      kind: 'clarification',
      fieldName: 'weight_kg',
      question: '你说的体重 100 是什么单位？',
      proposedValue: { source_value: 100 },
      options: [
        { id: 'kg', label: '100 公斤' },
        { id: 'jin', label: '100 斤' },
        { id: 'reject', label: '暂不写入' },
      ],
      customInputPlaceholder: '输入体重和单位',
    });

    expect(tree.root.findByProps({ children: '100 公斤' })).toBeTruthy();
    expect(tree.root.findByProps({ children: '100 斤' })).toBeTruthy();
    act(() =>
      tree.root.findByProps({ accessibilityLabel: '选择100 斤' }).props.onPress(),
    );
    expect(onAnswer).toHaveBeenCalledWith(baseCard.id, { optionId: 'jin' });

    act(() =>
      tree.root.findByProps({ accessibilityLabel: '填写其他健康档案内容' })
        .props.onPress(),
    );
    act(() => {
      tree.root.findByProps({ accessibilityLabel: '输入体重' }).props.onChangeText(
        '105',
      );
      tree.root.findByProps({ accessibilityLabel: '体重单位斤' }).props.onPress();
    });
    act(() =>
      tree.root.findByProps({ accessibilityLabel: '确认写入' }).props.onPress(),
    );
    expect(onAnswer).toHaveBeenLastCalledWith(baseCard.id, {
      customAnswer: {
        targetType: 'personal_profile',
        value: '105',
        unit: 'jin',
      },
    });
    act(() => tree.unmount());
  });

  test('shows the complete health fact form without sending the user to chat', () => {
    const { tree, onAnswer } = renderCard({
      ...baseCard,
      kind: 'clarification',
      targetType: 'health_fact',
      fieldName: 'allergy',
      operation: 'add',
      question: '请补充具体的过敏情况。',
      proposedValue: { fact_type: 'allergy' },
      options: [{ id: 'reject', label: '暂不写入' }],
      customInputPlaceholder: '例如：对青霉素过敏',
    });

    expect(tree.root.findAllByProps({ children: '过敏情况' }).length).toBeGreaterThan(0);
    act(() => {
      tree.root.findByProps({ accessibilityLabel: '输入健康情况' }).props.onChangeText(
        '对青霉素过敏',
      );
      tree.root.findByProps({ accessibilityLabel: '过去' }).props.onPress();
    });
    act(() =>
      tree.root.findByProps({ accessibilityLabel: '确认写入' }).props.onPress(),
    );
    expect(onAnswer).toHaveBeenCalledWith(baseCard.id, {
      customAnswer: {
        targetType: 'health_fact',
        statement: '对青霉素过敏',
        assertion: 'present',
        temporalStatus: 'past',
      },
    });
    act(() => tree.unmount());
  });

  test('keeps the deterministic reject option separate from custom input', () => {
    const { tree, onAnswer } = renderCard(baseCard);
    act(() =>
      tree.root.findByProps({ accessibilityLabel: '暂不写入健康档案' }).props
        .onPress(),
    );
    expect(onAnswer).toHaveBeenCalledWith(baseCard.id, { optionId: 'reject' });
    act(() => tree.unmount());
  });

  test('requires a whole number for age before submitting', () => {
    const { tree, onAnswer } = renderCard({
      ...baseCard,
      fieldName: 'age_years',
      question: '年龄是 28 岁吗？',
      proposedValue: { value: 28, canonical_unit: 'years' },
    });
    act(() =>
      tree.root.findByProps({ accessibilityLabel: '填写其他健康档案内容' })
        .props.onPress(),
    );
    act(() =>
      tree.root.findByProps({ accessibilityLabel: '输入年龄' }).props.onChangeText(
        '28.5',
      ),
    );

    expect(tree.root.findByProps({ children: '年龄请输入整数' })).toBeTruthy();
    expect(
      tree.root.findByProps({ accessibilityLabel: '确认写入' }).props.disabled,
    ).toBe(true);
    expect(onAnswer).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });

  test('disables every other inline editor while any card action is running', () => {
    const weightCard: HealthProfileCard = {
      ...baseCard,
      id: '019b4444-4444-7444-8444-444444444444',
      kind: 'clarification',
      fieldName: 'weight_kg',
      question: '请补充体重。',
      proposedValue: { source_value: 100 },
      options: [{ id: 'reject', label: '暂不写入' }],
    };
    const factCard: HealthProfileCard = {
      ...baseCard,
      id: '019b5555-5555-7555-8555-555555555555',
      kind: 'clarification',
      targetType: 'health_fact',
      fieldName: 'allergy',
      operation: 'add',
      question: '请补充过敏情况。',
      proposedValue: {
        statement: '对花生过敏',
        assertion: 'present',
        temporal_status: 'current',
      },
      options: [{ id: 'reject', label: '暂不写入' }],
    };
    const onAnswer = jest.fn();
    let tree: ReturnType<typeof create>;

    act(() => {
      tree = create(
        <HealthProfileCards
          actingCardId={baseCard.id}
          cards={[baseCard, weightCard, factCard]}
          error={null}
          loading={false}
          onAnswer={onAnswer}
        />,
      );
    });

    const submitButtons = tree!.root.findAllByProps({
      accessibilityLabel: '确认写入',
    });
    expect(
      submitButtons.filter((button) => button.props.disabled === true),
    ).toHaveLength(2);
    expect(
      tree!.root.findByProps({ accessibilityLabel: '输入体重' }).props.editable,
    ).toBe(false);
    expect(
      tree!.root.findByProps({ accessibilityLabel: '输入健康情况' }).props
        .editable,
    ).toBe(false);
    expect(onAnswer).not.toHaveBeenCalled();
    act(() => tree!.unmount());
  });
});
