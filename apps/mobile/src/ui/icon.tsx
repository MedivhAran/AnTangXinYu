import { HugeiconsIcon, type IconSvgElement } from '@hugeicons/react-native';

import { colors } from './theme';

type Props = {
  color?: string;
  icon: IconSvgElement;
  size?: number;
};

export function AppIcon({ color = colors.ink, icon, size = 24 }: Props) {
  return (
    <HugeiconsIcon
      accessibilityElementsHidden
      color={color}
      icon={icon}
      pointerEvents="none"
      size={size}
      strokeWidth={1.8}
    />
  );
}
