# Health Connect 变更游标失效后的恢复

## 依据与故障

Android 的 Changes Token 自生成起最多有效 30 天；过期时 `getChanges` 返回 `changesTokenExpired`。当前 App 原本在该状态直接报错，因而 `ExerciseSession` 过期后，排在它后面的睡眠、心率等类型也无法完成本轮同步。[Android API](https://developer.android.com/reference/androidx/health/connect/client/HealthConnectClient#getChangesToken(androidx.health.connect.client.changes.ChangesTokenRequest))、[同步建议](https://developer.android.com/health-and-fitness/health-connect/sync-data)

Android 官方建议在过期后重新读取并按记录 ID 去重，再建立新游标。普通读取权限可能只覆盖有限历史；读取完整历史需要用户授予 `READ_HEALTH_DATA_HISTORY`，且要检查设备是否支持该能力。[读取范围与历史权限](https://developer.android.com/health-and-fitness/health-connect/read-data)

## 当前恢复流程

1. 后台任务发现过期后标记为需要前台处理。前台向用户申请历史读取权限；原生模块核实权限确已授予。重装后本地游标消失时，如果服务器已有该类型的 Zepp 记录，也在前台走完整核对。`react-native-health-connect` 3.5.3 接受该权限请求，但其 `getGrantedPermissions()` 返回值未列出该特殊权限，所以核实由项目的小型原生模块完成。
2. 只读取当前登录用户、Zepp 来源和本次记录类型在服务器上的有效记录 ID，分页获取。Gadgetbridge 记录不参与这次核对。
3. 先取得新变更游标，再分页读取当前手机上可见的 Zepp 全部历史。每页按原 ID 上传；之后处理读历史期间发生的新增、修改和删除。
4. 完整读取与上传成功后，将服务器仍存在、但 Health Connect 全量结果中缺失的 ID 通过现有导入接口标记删除。服务器保留导入批次、删除墓碑和审计信息。
5. 只有上述步骤全部成功，才保存新游标。中断、拒绝权限、读取失败或服务器上传失败会保留原游标。完整读取结果为空时，该类型的现有服务器记录会全部标记删除；此行为限于用户确认只有当前手机上传过 Zepp 记录的账号。

恢复不会改变没有过期的记录类型的正常增量流程。历史读取可能耗时较长；验证应覆盖中断重试、权限拒绝、分页、读取期间的变更、服务器缺失 ID 和空结果。新原生权限与模块要求重新构建并安装 Android 包。

## 当前适用边界

服务器旧记录没有记录手机安装实例。自动核对缺失 ID 适用于同一账号的 Zepp 数据只由当前手机上传的情况；若同一账号曾从其他手机上传，当前手机读不到其他手机的记录，不能据此推断这些记录已被删除。多手机使用前需要给导入批次和观测增加安装来源标识，并以该标识限定核对范围。
