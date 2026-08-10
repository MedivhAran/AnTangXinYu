import {
  ArrowRight01Icon,
  FolderHeartIcon,
  Message01Icon,
  Settings04Icon,
} from '@hugeicons/core-free-icons';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { AppIcon } from '../ui/icon';
import { colors, radii, spacing, typefaces } from '../ui/theme';

export type ConversationAnchor = {
  group: string;
  id: string;
  preview: string;
  time: string;
};

type Props = {
  conversations: ConversationAnchor[];
  onClose: () => void;
  onOpenHealth: () => void;
  onOpenSettings: () => void;
  onSelectConversation: (messageId: string) => void;
  username: string;
  visible: boolean;
};

export function AppMenu({
  conversations,
  onClose,
  onOpenHealth,
  onOpenSettings,
  onSelectConversation,
  username,
  visible,
}: Props) {
  let previousGroup: string | null = null;

  return (
    <Modal
      animationType="fade"
      onRequestClose={onClose}
      statusBarTranslucent
      transparent
      visible={visible}
    >
      <View style={styles.overlay}>
        <Pressable
          accessibilityLabel="关闭菜单"
          accessibilityRole="button"
          onPress={onClose}
          style={StyleSheet.absoluteFill}
        />
        <SafeAreaView edges={['top', 'bottom', 'left']} style={styles.panel}>
          <View style={styles.accountRow}>
            <View style={styles.avatar}>
              <Text style={styles.avatarText}>{username.slice(0, 1).toUpperCase()}</Text>
            </View>
            <View style={styles.accountCopy}>
              <Text numberOfLines={1} style={styles.username}>
                {username}
              </Text>
              <Text style={styles.accountHint}>安糖心语账号</Text>
            </View>
            <Pressable
              accessibilityLabel="打开设置"
              accessibilityRole="button"
              onPress={onOpenSettings}
              style={({ pressed }) => [
                styles.settingsButton,
                pressed && styles.pressed,
              ]}
            >
              <AppIcon icon={Settings04Icon} />
            </Pressable>
          </View>

          <Pressable
            accessibilityLabel="健康档案"
            accessibilityRole="button"
            onPress={onOpenHealth}
            style={({ pressed }) => [
              styles.healthEntry,
              pressed && styles.healthEntryPressed,
            ]}
          >
            <View style={styles.healthIcon}>
              <AppIcon color={colors.primaryPressed} icon={FolderHeartIcon} size={26} />
            </View>
            <View style={styles.healthCopy}>
              <Text style={styles.healthTitle}>健康档案</Text>
              <Text style={styles.healthHint}>档案、手环与健康记录</Text>
            </View>
            <AppIcon color={colors.muted} icon={ArrowRight01Icon} size={20} />
          </Pressable>

          <View style={styles.sectionHeading}>
            <AppIcon color={colors.muted} icon={Message01Icon} size={19} />
            <Text style={styles.sectionTitle}>对话记录</Text>
          </View>

          <ScrollView
            contentContainerStyle={styles.conversationList}
            showsVerticalScrollIndicator={false}
          >
            {conversations.length === 0 ? (
              <Text style={styles.emptyHistory}>开始聊天后，对话位置会显示在这里。</Text>
            ) : (
              conversations.map((conversation) => {
                const showGroup = conversation.group !== previousGroup;
                previousGroup = conversation.group;
                return (
                  <View key={conversation.id}>
                    {showGroup ? (
                      <Text style={styles.groupTitle}>{conversation.group}</Text>
                    ) : null}
                    <Pressable
                      accessibilityLabel={`${conversation.time} ${conversation.preview}`}
                      accessibilityRole="menuitem"
                      onPress={() => onSelectConversation(conversation.id)}
                      style={({ pressed }) => [
                        styles.conversation,
                        pressed && styles.conversationPressed,
                      ]}
                    >
                      <Text style={styles.conversationTime}>{conversation.time}</Text>
                      <Text numberOfLines={1} style={styles.conversationPreview}>
                        {conversation.preview}
                      </Text>
                    </Pressable>
                  </View>
                );
              })
            )}
          </ScrollView>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    flexDirection: 'row',
    backgroundColor: 'rgba(10, 28, 25, 0.34)',
  },
  panel: {
    width: '86%',
    maxWidth: 400,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.md,
    paddingBottom: spacing.sm,
    backgroundColor: colors.background,
    boxShadow: '8px 0 30px rgba(23, 48, 46, 0.16)',
  },
  accountRow: {
    minHeight: 64,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  avatar: {
    width: 48,
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  avatarText: {
    color: colors.white,
    fontFamily: typefaces.sansMedium,
    fontSize: 19,
  },
  accountCopy: { flex: 1, gap: 2 },
  username: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 18,
  },
  accountHint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  settingsButton: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
    backgroundColor: colors.paper,
  },
  pressed: { opacity: 0.64 },
  healthEntry: {
    minHeight: 82,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginTop: spacing.lg,
    paddingHorizontal: spacing.md,
    borderWidth: 1,
    borderColor: '#CBEAE2',
    borderRadius: radii.lg,
    borderCurve: 'continuous',
    backgroundColor: colors.primarySoft,
  },
  healthEntryPressed: { opacity: 0.74, transform: [{ scale: 0.99 }] },
  healthIcon: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.md,
    backgroundColor: colors.paper,
  },
  healthCopy: { flex: 1, gap: 3 },
  healthTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 16,
  },
  healthHint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  sectionHeading: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    paddingTop: spacing.xl,
    paddingHorizontal: spacing.xs,
  },
  sectionTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 15,
  },
  conversationList: { paddingTop: spacing.sm, paddingBottom: spacing.lg },
  emptyHistory: {
    padding: spacing.md,
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 20,
  },
  groupTitle: {
    paddingHorizontal: spacing.xs,
    paddingTop: spacing.sm,
    paddingBottom: spacing.xs,
    color: colors.faint,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
  },
  conversation: {
    minHeight: 46,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.xs,
    borderRadius: radii.md,
  },
  conversationPressed: { backgroundColor: colors.paperMuted },
  conversationTime: {
    width: 42,
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 12,
    fontVariant: ['tabular-nums'],
  },
  conversationPreview: {
    flex: 1,
    color: colors.text,
    fontFamily: typefaces.sans,
    fontSize: 14,
  },
});
