import { SafeAreaProvider } from 'react-native-safe-area-context';

import { appClient } from './src/api/app-client';
import type { User } from './src/api/types';
import { ChatScreen } from './src/chat/ChatScreen';

// 演示用的虚拟用户，不需要登录
const DEMO_USER = {
  id: 'demo-user',
  username: '演示用户',
} as User;

export default function App() {
  return (
    <SafeAreaProvider>
      <ChatScreen
        api={appClient}
        user={DEMO_USER}
        onSignedOut={() => {
          // 演示模式不处理登出
        }}
      />
    </SafeAreaProvider>
  );
}