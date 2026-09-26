import { requireOptionalNativeModule } from 'expo';
import { Platform } from 'react-native';

type NativeModule = {
  isBackgroundReadAvailable(): Promise<boolean>;
  isHistoryReadAvailable(): Promise<boolean>;
  hasHistoryReadPermission(): Promise<boolean>;
};

const nativeModule =
  requireOptionalNativeModule<NativeModule>('HealthConnectBackground');

export function isBackgroundReadAvailable(): Promise<boolean> {
  if (Platform.OS !== 'android') return Promise.resolve(false);
  if (nativeModule === null) {
    throw new Error(
      'Health Connect 后台能力模块未编译，请重新生成 Android 开发构建',
    );
  }
  return nativeModule.isBackgroundReadAvailable();
}

export function isHistoryReadAvailable(): Promise<boolean> {
  if (Platform.OS !== 'android') return Promise.resolve(false);
  if (nativeModule === null) {
    throw new Error('Health Connect 历史读取模块未编译，请安装新版 Android 包');
  }
  return nativeModule.isHistoryReadAvailable();
}

export function hasHistoryReadPermission(): Promise<boolean> {
  if (Platform.OS !== 'android') return Promise.resolve(false);
  if (nativeModule === null) {
    throw new Error('Health Connect 历史读取模块未编译，请安装新版 Android 包');
  }
  return nativeModule.hasHistoryReadPermission();
}
