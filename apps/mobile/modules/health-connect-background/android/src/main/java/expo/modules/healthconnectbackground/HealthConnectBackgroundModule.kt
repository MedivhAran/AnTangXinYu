package expo.modules.healthconnectbackground

import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.HealthConnectFeatures
import androidx.health.connect.client.feature.ExperimentalFeatureAvailabilityApi
import androidx.health.connect.client.permission.HealthPermission
import expo.modules.kotlin.functions.Coroutine
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

@OptIn(ExperimentalFeatureAvailabilityApi::class)
class HealthConnectBackgroundModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("HealthConnectBackground")

    AsyncFunction("isBackgroundReadAvailable") {
      val context = requireNotNull(appContext.reactContext) {
        "React context is unavailable"
      }
      val features = HealthConnectClient.getOrCreate(context).features
      features.getFeatureStatus(
        HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_IN_BACKGROUND
      ) == HealthConnectFeatures.FEATURE_STATUS_AVAILABLE
    }

    AsyncFunction("isHistoryReadAvailable") {
      val context = requireNotNull(appContext.reactContext) {
        "React context is unavailable"
      }
      HealthConnectClient.getOrCreate(context).features.getFeatureStatus(
        HealthConnectFeatures.FEATURE_READ_HEALTH_DATA_HISTORY
      ) == HealthConnectFeatures.FEATURE_STATUS_AVAILABLE
    }

    AsyncFunction("hasHistoryReadPermission") Coroutine { ->
      val context = requireNotNull(appContext.reactContext) {
        "React context is unavailable"
      }
      HealthConnectClient.getOrCreate(context).permissionController
        .getGrantedPermissions()
        .contains(HealthPermission.PERMISSION_READ_HEALTH_DATA_HISTORY)
    }
  }
}
