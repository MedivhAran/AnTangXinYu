from uuid import UUID


class HealthProfileError(Exception):
    """健康档案模块的可预期错误基类。"""


class HealthProfileInvariantError(HealthProfileError):
    """数据库中缺少必须存在的关联记录。"""


class InvalidProfileProposalError(HealthProfileError):
    """结构合法但不能按当前档案执行的候选修改。"""


class ProfileCardNotFoundError(HealthProfileError):
    """当前用户没有这张档案卡片。"""


class ProfileCardDecisionConflictError(HealthProfileError):
    """卡片已经被另一个操作处理，或幂等键被用于其他操作。"""


class InvalidProfileCardDecisionError(HealthProfileError):
    """对卡片执行了它不支持的决定。"""


class HealthProfileChangedError(HealthProfileError):
    """用户编辑期间，目标档案已经发生变化。"""


class ProfileClientActionConflictError(HealthProfileError):
    """同一幂等操作 ID 被用于不同请求。"""


class WearableImportConflictError(HealthProfileError):
    """同一同步 ID 对应不同请求，或同版本设备记录内容不一致。"""

    def __init__(self, code: str, *, record_id: str | None = None) -> None:
        self.code = code
        self.record_id = record_id
        super().__init__(code)


class WearableObservationNotFoundError(HealthProfileError):
    def __init__(self, observation_id: UUID) -> None:
        self.observation_id = observation_id
        super().__init__(str(observation_id))
