import { act, create } from 'react-test-renderer';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { ApiClient } from '../api/client';
import type { User } from '../api/types';
import { AuthScreen } from './AuthScreen';

// Resolve React Native's lazy component exports while this module loads so the
// assertions below measure the screen behavior rather than module transforms.
void [ActivityIndicator, Pressable, ScrollView, Text, TextInput, View];

const user: User = {
  id: '0198e4cb-0ae7-7a8c-b800-4c6fa9999999',
  username: 'alice',
};

function createApi() {
  return {
    login: jest.fn().mockResolvedValue(user),
    register: jest.fn().mockResolvedValue(user),
  } as unknown as ApiClient;
}

describe('AuthScreen', () => {
  test('keeps login validation and submits trimmed credentials', async () => {
    const api = createApi();
    const onAuthenticated = jest.fn();
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <AuthScreen api={api} onAuthenticated={onAuthenticated} />,
      );
    });

    expect(
      tree!.root.findByProps({ accessibilityLabel: '切换到登录' }).props
        .accessibilityState,
    ).toEqual({ selected: true });

    act(() => {
      tree!.root
        .findByProps({ accessibilityLabel: '用户名' })
        .props.onChangeText('  alice  ');
      tree!.root
        .findByProps({ accessibilityLabel: '密码' })
        .props.onChangeText('safe-pass');
    });

    const submit = tree!.root.findByProps({
      accessibilityLabel: '登录并进入安糖心语',
    });
    expect(submit.props.accessibilityState).toEqual({
      busy: false,
      disabled: false,
    });

    await act(async () => {
      submit.props.onPress();
    });

    expect(api.login).toHaveBeenCalledWith('alice', 'safe-pass');
    expect(onAuthenticated).toHaveBeenCalledWith(user);
    act(() => tree!.unmount());
  });

  test('switching modes clears password and submits registration', async () => {
    const api = createApi();
    const onAuthenticated = jest.fn();
    let tree: ReturnType<typeof create>;

    await act(async () => {
      tree = create(
        <AuthScreen
          api={api}
          initialMessage="登录状态已过期"
          onAuthenticated={onAuthenticated}
        />,
      );
    });

    act(() => {
      tree!.root
        .findByProps({ accessibilityLabel: '密码' })
        .props.onChangeText('old-password');
      tree!.root
        .findByProps({ accessibilityLabel: '切换到注册' })
        .props.onPress();
    });

    expect(
      tree!.root.findByProps({ accessibilityLabel: '密码' }).props.value,
    ).toBe('');
    expect(
      tree!.root.findAllByProps({ children: '登录状态已过期' }),
    ).toHaveLength(0);
    expect(
      tree!.root.findByProps({ accessibilityLabel: '切换到注册' }).props
        .accessibilityState,
    ).toEqual({ selected: true });

    act(() => {
      tree!.root
        .findByProps({ accessibilityLabel: '用户名' })
        .props.onChangeText('alice');
      tree!.root
        .findByProps({ accessibilityLabel: '密码' })
        .props.onChangeText('new-password');
    });

    await act(async () => {
      tree!.root
        .findByProps({ accessibilityLabel: '创建安糖心语账号' })
        .props.onPress();
    });

    expect(api.register).toHaveBeenCalledWith('alice', 'new-password');
    expect(api.login).not.toHaveBeenCalled();
    expect(onAuthenticated).toHaveBeenCalledWith(user);
    act(() => tree!.unmount());
  });
});
