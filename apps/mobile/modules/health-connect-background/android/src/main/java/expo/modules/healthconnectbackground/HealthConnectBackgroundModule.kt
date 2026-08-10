package expo.modules.healthconnectbackground

import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.HealthConnectFeatures
import androidx.health.connect.client.feature.ExperimentalFeatureAvailabilityApi
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
  }
}
