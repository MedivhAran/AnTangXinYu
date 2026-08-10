export type User = {
  id: string;
  username: string;
};

export type TokenPair = {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
};

export type AuthResult = {
  user: User;
  tokens: TokenPair;
};

export type MessageRole = 'user' | 'assistant';
export type MessageStatus = 'generating' | 'completed' | 'failed' | 'cancelled';

export type ChatSource = {
  sourceId: string;
  title: string;
  url: string;
};

export type ChatMessage = {
  id: string;
  clientMessageId: string | null;
  role: MessageRole;
  status: MessageStatus;
  content: string;
  sources: ChatSource[];
  createdAt: string;
  completedAt: string | null;
};

export type MessageHistory = {
  messages: ChatMessage[];
  nextBefore: string | null;
};

export type ProactiveCareNotificationData = {
  kind: 'routine_check_in' | 'plan_follow_up' | 'health_event';
  messageId: string;
  deliveryId: string;
};

export type RoutineCareCadence =
  | 'disabled'
  | 'daily'
  | 'every_3_days'
  | 'weekly';

export type ProactiveCareSettings = {
  routineCadence: RoutineCareCadence;
  planFollowUpEnabled: boolean;
  healthEventsEnabled: boolean;
  timezone: string;
  quietHoursStart: string;
  quietHoursEnd: string;
  healthNotificationPreviewEnabled: boolean;
};

export type HealthProfileCard = {
  id: string;
  kind: 'confirmation' | 'clarification';
  targetType: 'personal_profile' | 'health_fact';
  fieldName: string;
  operation: 'set' | 'clear' | 'add' | 'update' | 'retract';
  question: string;
  proposedValue: Record<string, unknown> | null;
  options: HealthProfileCardOption[];
  allowCustomInput: boolean;
  customInputPlaceholder: string | null;
  createdAt: string;
};

export type HealthProfileCardOption = {
  id: string;
  label: string;
};

export type HealthProfileCards = {
  cards: HealthProfileCard[];
};

export type HealthProfileCardDecision = {
  cardId: string;
  status: 'applied' | 'rejected' | 'conflicted';
};

export type PersonalProfileField =
  | 'sex'
  | 'age_years'
  | 'height_cm'
  | 'weight_kg'
  | 'resident_area'
  | 'schedule_type'
  | 'occupation';

export type PersonalProfileUnit = 'years' | 'cm' | 'm' | 'kg' | 'jin' | 'lb';

export type HealthProfileCustomAnswer =
  | {
      targetType: 'personal_profile';
      value: string;
      unit?: PersonalProfileUnit;
    }
  | {
      targetType: 'health_fact';
      statement: string;
      assertion: 'present' | 'absent';
      temporalStatus: 'current' | 'past' | 'unknown';
    };

export type HealthProfileCardAnswer =
  | { optionId: string; customAnswer?: never }
  | { optionId?: never; customAnswer: HealthProfileCustomAnswer };

export type PersonalProfile = {
  sex: string | null;
  ageYears: number | null;
  ageAsOfDate: string | null;
  heightCm: number | null;
  weightKg: number | null;
  residentArea: string | null;
  scheduleType: string | null;
  occupation: string | null;
  revision: number;
  updatedAt: string;
};

export type HealthFactType =
  | 'medical_history'
  | 'allergy'
  | 'severe_hypoglycemia_history'
  | 'treatment';

export type HealthFact = {
  id: string;
  factType: HealthFactType;
  statement: string;
  assertion: 'present' | 'absent';
  temporalStatus: 'current' | 'past' | 'unknown';
  effectiveStart: string | null;
  effectiveEnd: string | null;
  revision: number;
  createdAt: string;
  updatedAt: string;
};

type HealthProfileChangeBase = {
  clientActionId: string;
  expectedRevision: number | null;
};

export type HealthProfileChange = HealthProfileChangeBase &
  (
    | {
        targetType: 'personal_profile';
        operation: 'set';
        fieldName: PersonalProfileField;
        value: string;
        unit?: PersonalProfileUnit;
      }
    | {
        targetType: 'personal_profile';
        operation: 'clear';
        fieldName: PersonalProfileField;
      }
    | {
        targetType: 'health_fact';
        operation: 'add';
        factType: HealthFactType;
        statement: string;
        assertion: HealthFact['assertion'];
        temporalStatus: HealthFact['temporalStatus'];
      }
    | {
        targetType: 'health_fact';
        operation: 'update';
        targetId: string;
        factType: HealthFactType;
        statement: string;
        assertion: HealthFact['assertion'];
        temporalStatus: HealthFact['temporalStatus'];
      }
    | {
        targetType: 'health_fact';
        operation: 'retract';
        targetId: string;
        factType: HealthFactType;
      }
  );

export type HealthProfileChangeResult = {
  changeId: string;
  status: 'applied';
  targetType: 'personal_profile' | 'health_fact';
  fieldName: string;
  operation: HealthProfileCard['operation'];
  targetId: string | null;
  resultRevision: number;
};

export type SleepStage = {
  startTime: string;
  endTime: string;
  stage:
  | 'unknown'
  | 'awake'
  | 'sleeping'
  | 'out_of_bed'
  | 'awake_in_bed'
  | 'light'
  | 'deep'
  | 'rem';
};

type WearableLatestBase = {
  observedAt: string;
  sourcePackage: string;
};

export type WearableLatest = WearableLatestBase &
  (
    | { recordType: 'steps'; data: { count: number } }
    | {
      recordType: 'exercise';
      data: { exerciseType: number; title: string | null };
    }
    | { recordType: 'distance'; data: { meters: number } }
    | { recordType: 'elevation_gained'; data: { meters: number } }
    | { recordType: 'weight'; data: { kilograms: number } }
    | {
      recordType: 'respiratory_rate';
      data: { breathsPerMinute: number };
    }
    | {
      recordType: 'resting_heart_rate';
      data: { beatsPerMinute: number };
    }
    | {
      recordType: 'heart_rate';
      data: {
        samples: { time: string; beatsPerMinute: number }[];
      };
    }
    | {
      recordType: 'sleep';
      data: { stages: SleepStage[] };
    }
    | {
      recordType: 'oxygen_saturation';
      data: { percentage: number };
    }
  );

export type HealthProfile = {
  personalProfile: PersonalProfile;
  healthFacts: HealthFact[];
  wearableLatest: WearableLatest[];
};

type WearableDevice = {
  manufacturer: string | null;
  model: string | null;
  device_type: number | null;
};

type WearableRecordBase = {
  external_record_id: string;
  start_time: string;
  end_time: string;
  start_zone_offset_seconds: number | null;
  end_zone_offset_seconds: number | null;
  source_package: string;
  recording_method: number | null;
  device: WearableDevice | null;
  source_last_modified_at: string;
};

export type WearableRecord = WearableRecordBase &
  (
    | { record_type: 'steps'; data: { count: number } }
    | {
      record_type: 'exercise';
      data: { exercise_type: number; title: string | null };
    }
    | { record_type: 'distance'; data: { meters: number } }
    | { record_type: 'elevation_gained'; data: { meters: number } }
    | { record_type: 'weight'; data: { kilograms: number } }
    | {
      record_type: 'respiratory_rate';
      data: { breaths_per_minute: number };
    }
    | {
      record_type: 'resting_heart_rate';
      data: { beats_per_minute: number };
    }
    | {
      record_type: 'heart_rate';
      data: {
        samples: { time: string; beats_per_minute: number }[];
      };
    }
    | {
      record_type: 'sleep';
      data: {
        stages: {
          start_time: string;
          end_time: string;
          stage:
          | 'unknown'
          | 'awake'
          | 'sleeping'
          | 'out_of_bed'
          | 'awake_in_bed'
          | 'light'
          | 'deep'
          | 'rem';
        }[];
      };
    }
    | { record_type: 'oxygen_saturation'; data: { percentage: number } }
  );

export type WearableImportResponse = {
  importId: string;
  status: 'completed';
  recordsCreated: number;
  recordsUpdated: number;
  recordsUnchanged: number;
  recordsDeleted: number;
};

export type MessageStartedEvent = {
  type: 'message_started';
  userMessageId: string;
  assistantMessageId: string;
  runId: string;
};

export type TextDeltaEvent = {
  type: 'text_delta';
  delta: string;
};

export type AgentActivityPhase =
  | 'thinking'
  | 'searching'
  | 'reading'
  | 'organizing';

export type AgentActivityEvent = {
  type: 'agent_activity';
  phase: AgentActivityPhase;
};

export type MessageCompletedEvent = {
  type: 'message_completed';
  inputTokens: number;
  outputTokens: number;
  sources: ChatSource[];
};

export type MessageFailedEvent = {
  type: 'message_failed';
  code: 'agent_run_failed';
  errorType: string;
};

export type ChatStreamEvent =
  | MessageStartedEvent
  | TextDeltaEvent
  | AgentActivityEvent
  | MessageCompletedEvent
  | MessageFailedEvent;

type JsonObject = Record<string, unknown>;

const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function objectValue(value: unknown, name: string): JsonObject {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`${name} 必须是 JSON 对象`);
  }
  return value as JsonObject;
}

function stringValue(value: unknown, name: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${name} 必须是非空字符串`);
  }
  return value;
}

function uuidValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  if (!uuidPattern.test(result)) throw new Error(`${name} 必须是 UUID`);
  return result;
}

function integerValue(value: unknown, name: string): number {
  if (!Number.isInteger(value) || (value as number) < 0) {
    throw new Error(`${name} 必须是非负整数`);
  }
  return value as number;
}

function booleanValue(value: unknown, name: string): boolean {
  if (typeof value !== 'boolean') throw new Error(`${name} 必须是布尔值`);
  return value;
}

function quietTimeValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(result)) {
    throw new Error(`${name} 必须使用 HH:MM`);
  }
  return result;
}

function ianaTimeZoneValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  try {
    new Intl.DateTimeFormat('zh-CN', { timeZone: result }).format();
  } catch {
    throw new Error(`${name} 必须是有效的 IANA 时区`);
  }
  return result;
}

function dateValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  if (Number.isNaN(Date.parse(result))) throw new Error(`${name} 必须是日期时间`);
  return result;
}

function zonedDateTimeValue(value: unknown, name: string): string {
  const result = dateValue(value, name);
  if (
    !result.includes('T') ||
    !/(?:Z|[+-]\d{2}:\d{2})$/i.test(result)
  ) {
    throw new Error(`${name} 必须包含时区`);
  }
  return result;
}

function dateOnlyValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  const timestamp = Date.parse(`${result}T00:00:00.000Z`);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(result) ||
    Number.isNaN(timestamp) ||
    new Date(timestamp).toISOString().slice(0, 10) !== result
  ) {
    throw new Error(`${name} 必须是 YYYY-MM-DD 日期`);
  }
  return result;
}

function nullableStringValue(value: unknown, name: string): string | null {
  return value === null ? null : stringValue(value, name);
}

function nullableDateOnlyValue(value: unknown, name: string): string | null {
  return value === null ? null : dateOnlyValue(value, name);
}

function decimalNumberValue(
  value: unknown,
  name: string,
  minimum: number,
  minimumInclusive: boolean,
): number {
  if (
    typeof value !== 'string' ||
    !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value)
  ) {
    throw new Error(`${name} 必须是十进制数字字符串`);
  }
  const result = Number(value);
  const validMinimum = minimumInclusive
    ? result >= minimum
    : result > minimum;
  if (!Number.isFinite(result) || !validMinimum) {
    throw new Error(
      `${name} 必须是${minimumInclusive ? '不小于' : '大于'} ${minimum} 的有限数字`,
    );
  }
  return result;
}

function positiveIntegerValue(value: unknown, name: string): number {
  const result = integerValue(value, name);
  if (result === 0) throw new Error(`${name} 必须是正整数`);
  return result;
}

function httpUrlValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  let parsed: URL;
  try {
    parsed = new URL(result);
  } catch {
    throw new Error(`${name} 必须是有效 URL`);
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error(`${name} 只允许 HTTP 或 HTTPS`);
  }
  return result;
}

function parseSources(value: unknown, name: string): ChatSource[] {
  if (!Array.isArray(value)) throw new Error(`${name} 必须是数组`);

  const knownIds = new Set<string>();
  return value.map((source, index) => {
    const data = objectValue(source, `${name}[${index}]`);
    const sourceId = stringValue(data.source_id, `${name}[${index}].source_id`);
    if (!/^S[1-9]\d*$/.test(sourceId)) {
      throw new Error(`${name}[${index}].source_id 必须是 S 加正整数`);
    }
    if (knownIds.has(sourceId)) throw new Error(`${name} 包含重复的 ${sourceId}`);
    knownIds.add(sourceId);

    return {
      sourceId,
      title: stringValue(data.title, `${name}[${index}].title`),
      url: httpUrlValue(data.url, `${name}[${index}].url`),
    };
  });
}

export function parseUser(value: unknown): User {
  const data = objectValue(value, 'user');
  return {
    id: uuidValue(data.id, 'user.id'),
    username: stringValue(data.username, 'user.username'),
  };
}

export function parseTokenPair(value: unknown): TokenPair {
  const data = objectValue(value, 'token pair');
  return {
    accessToken: stringValue(data.access_token, 'access_token'),
    refreshToken: stringValue(data.refresh_token, 'refresh_token'),
    expiresIn: integerValue(data.expires_in, 'expires_in'),
  };
}

export function parseAuthResult(value: unknown): AuthResult {
  const data = objectValue(value, 'auth response');
  return {
    user: parseUser(data.user),
    tokens: parseTokenPair(data),
  };
}

function parseMessage(value: unknown): ChatMessage {
  const data = objectValue(value, 'message');
  const role = stringValue(data.role, 'message.role');
  const status = stringValue(data.status, 'message.status');

  if (role !== 'user' && role !== 'assistant') {
    throw new Error(`未知消息角色：${role}`);
  }
  if (!['generating', 'completed', 'failed', 'cancelled'].includes(status)) {
    throw new Error(`未知消息状态：${status}`);
  }
  if (typeof data.content !== 'string') {
    throw new Error('message.content 必须是字符串');
  }

  return {
    id: uuidValue(data.id, 'message.id'),
    clientMessageId:
      data.client_message_id === null
        ? null
        : uuidValue(data.client_message_id, 'message.client_message_id'),
    role,
    status: status as MessageStatus,
    content: data.content,
    sources: parseSources(data.sources, 'message.sources'),
    createdAt: dateValue(data.created_at, 'message.created_at'),
    completedAt:
      data.completed_at === null
        ? null
        : dateValue(data.completed_at, 'message.completed_at'),
  };
}

export function parseMessageHistory(value: unknown): MessageHistory {
  const data = objectValue(value, 'message history');
  if (!Array.isArray(data.messages)) {
    throw new Error('message history.messages 必须是数组');
  }
  return {
    messages: data.messages.map(parseMessage),
    nextBefore:
      data.next_before === null
        ? null
        : uuidValue(data.next_before, 'message history.next_before'),
  };
}

export function parseProactiveCareNotificationData(
  value: unknown,
): ProactiveCareNotificationData {
  const data = objectValue(value, 'notification');
  const kind = stringValue(data.kind, 'notification.kind');
  if (!['routine_check_in', 'plan_follow_up', 'health_event'].includes(kind)) {
    throw new Error('notification.kind 无效');
  }
  return {
    kind: kind as ProactiveCareNotificationData['kind'],
    messageId: uuidValue(data.message_id, 'notification.message_id'),
    deliveryId: uuidValue(data.delivery_id, 'notification.delivery_id'),
  };
}

export function parseProactiveCareSettings(
  value: unknown,
): ProactiveCareSettings {
  const data = objectValue(value, 'care settings');
  const cadence = stringValue(
    data.routine_cadence,
    'care settings.routine_cadence',
  );
  if (!['disabled', 'daily', 'every_3_days', 'weekly'].includes(cadence)) {
    throw new Error('care settings.routine_cadence 无效');
  }
  return {
    routineCadence: cadence as RoutineCareCadence,
    planFollowUpEnabled: booleanValue(
      data.plan_follow_up_enabled,
      'care settings.plan_follow_up_enabled',
    ),
    healthEventsEnabled: booleanValue(
      data.health_events_enabled,
      'care settings.health_events_enabled',
    ),
    timezone: ianaTimeZoneValue(data.timezone, 'care settings.timezone'),
    quietHoursStart: quietTimeValue(
      data.quiet_hours_start,
      'care settings.quiet_hours_start',
    ),
    quietHoursEnd: quietTimeValue(
      data.quiet_hours_end,
      'care settings.quiet_hours_end',
    ),
    healthNotificationPreviewEnabled: booleanValue(
      data.health_notification_preview_enabled,
      'care settings.health_notification_preview_enabled',
    ),
  };
}

function parsePersonalProfile(value: unknown): PersonalProfile {
  const data = objectValue(value, 'health profile.personal_profile');
  const ageYears =
    data.age_years === null
      ? null
      : integerValue(data.age_years, 'health profile.personal_profile.age_years');
  if (ageYears !== null && ageYears > 150) {
    throw new Error('health profile.personal_profile.age_years 必须不大于 150');
  }

  return {
    sex: nullableStringValue(data.sex, 'health profile.personal_profile.sex'),
    ageYears,
    ageAsOfDate: nullableDateOnlyValue(
      data.age_as_of_date,
      'health profile.personal_profile.age_as_of_date',
    ),
    heightCm:
      data.height_cm === null
        ? null
        : decimalNumberValue(
            data.height_cm,
            'health profile.personal_profile.height_cm',
            0,
            false,
          ),
    weightKg:
      data.weight_kg === null
        ? null
        : decimalNumberValue(
            data.weight_kg,
            'health profile.personal_profile.weight_kg',
            0,
            false,
          ),
    residentArea: nullableStringValue(
      data.resident_area,
      'health profile.personal_profile.resident_area',
    ),
    scheduleType: nullableStringValue(
      data.schedule_type,
      'health profile.personal_profile.schedule_type',
    ),
    occupation: nullableStringValue(
      data.occupation,
      'health profile.personal_profile.occupation',
    ),
    revision: integerValue(
      data.revision,
      'health profile.personal_profile.revision',
    ),
    updatedAt: zonedDateTimeValue(
      data.updated_at,
      'health profile.personal_profile.updated_at',
    ),
  };
}

function parseHealthFact(value: unknown, index: number): HealthFact {
  const name = `health profile.health_facts[${index}]`;
  const data = objectValue(value, name);
  const factType = stringValue(data.fact_type, `${name}.fact_type`);
  if (
    ![
      'medical_history',
      'allergy',
      'severe_hypoglycemia_history',
      'treatment',
    ].includes(factType)
  ) {
    throw new Error(`${name}.fact_type 无效`);
  }
  const assertion = stringValue(data.assertion, `${name}.assertion`);
  if (assertion !== 'present' && assertion !== 'absent') {
    throw new Error(`${name}.assertion 无效`);
  }
  const temporalStatus = stringValue(
    data.temporal_status,
    `${name}.temporal_status`,
  );
  if (!['current', 'past', 'unknown'].includes(temporalStatus)) {
    throw new Error(`${name}.temporal_status 无效`);
  }
  const effectiveStart = nullableDateOnlyValue(
    data.effective_start,
    `${name}.effective_start`,
  );
  const effectiveEnd = nullableDateOnlyValue(
    data.effective_end,
    `${name}.effective_end`,
  );
  if (
    effectiveStart !== null &&
    effectiveEnd !== null &&
    effectiveEnd < effectiveStart
  ) {
    throw new Error(`${name} 的结束日期不能早于开始日期`);
  }

  return {
    id: uuidValue(data.id, `${name}.id`),
    factType: factType as HealthFactType,
    statement: stringValue(data.statement, `${name}.statement`),
    assertion,
    temporalStatus: temporalStatus as HealthFact['temporalStatus'],
    effectiveStart,
    effectiveEnd,
    revision: integerValue(data.revision, `${name}.revision`),
    createdAt: zonedDateTimeValue(data.created_at, `${name}.created_at`),
    updatedAt: zonedDateTimeValue(data.updated_at, `${name}.updated_at`),
  };
}

function parseWearableLatest(value: unknown, index: number): WearableLatest {
  const name = `health profile.wearable_latest[${index}]`;
  const payload = objectValue(value, name);
  const recordType = stringValue(payload.record_type, `${name}.record_type`);
  const observedAt = zonedDateTimeValue(
    payload.observed_at,
    `${name}.observed_at`,
  );
  const sourcePackage = stringValue(
    payload.source_package,
    `${name}.source_package`,
  );
  const data = objectValue(payload.data, `${name}.data`);

  switch (recordType) {
    case 'steps':
      return {
        recordType,
        observedAt,
        sourcePackage,
        data: { count: integerValue(data.count, `${name}.data.count`) },
      };
    case 'exercise':
      return {
        recordType,
        observedAt,
        sourcePackage,
        data: {
          exerciseType: integerValue(
            data.exercise_type,
            `${name}.data.exercise_type`,
          ),
          title: nullableStringValue(data.title, `${name}.data.title`),
        },
      };
    case 'distance':
    case 'elevation_gained':
      return {
        recordType,
        observedAt,
        sourcePackage,
        data: {
          meters: decimalNumberValue(
            data.meters,
            `${name}.data.meters`,
            0,
            true,
          ),
        },
      };
    case 'weight':
      return {
        recordType,
        observedAt,
        sourcePackage,
        data: {
          kilograms: decimalNumberValue(
            data.kilograms,
            `${name}.data.kilograms`,
            0,
            false,
          ),
        },
      };
    case 'respiratory_rate':
      return {
        recordType,
        observedAt,
        sourcePackage,
        data: {
          breathsPerMinute: decimalNumberValue(
            data.breaths_per_minute,
            `${name}.data.breaths_per_minute`,
            0,
            false,
          ),
        },
      };
    case 'resting_heart_rate':
      return {
        recordType,
        observedAt,
        sourcePackage,
        data: {
          beatsPerMinute: positiveIntegerValue(
            data.beats_per_minute,
            `${name}.data.beats_per_minute`,
          ),
        },
      };
    case 'heart_rate': {
      if (!Array.isArray(data.samples) || data.samples.length === 0) {
        throw new Error(`${name}.data.samples 必须是非空数组`);
      }
      let previousTime = Number.NEGATIVE_INFINITY;
      const samples = data.samples.map((value, sampleIndex) => {
        const sampleName = `${name}.data.samples[${sampleIndex}]`;
        const sample = objectValue(value, sampleName);
        const time = zonedDateTimeValue(sample.time, `${sampleName}.time`);
        const timestamp = Date.parse(time);
        if (timestamp <= previousTime) {
          throw new Error(`${name}.data.samples 必须按时间升序且时间不能重复`);
        }
        if (timestamp > Date.parse(observedAt)) {
          throw new Error(`${sampleName}.time 不能晚于 observed_at`);
        }
        previousTime = timestamp;
        return {
          time,
          beatsPerMinute: positiveIntegerValue(
            sample.beats_per_minute,
            `${sampleName}.beats_per_minute`,
          ),
        };
      });
      return {
        recordType,
        observedAt,
        sourcePackage,
        data: { samples },
      };
    }
    case 'sleep': {
      if (!Array.isArray(data.stages)) {
        throw new Error(`${name}.data.stages 必须是数组`);
      }
      let previousEnd = Number.NEGATIVE_INFINITY;
      const stages = data.stages.map((value, stageIndex) => {
        const stageName = `${name}.data.stages[${stageIndex}]`;
        const stageData = objectValue(value, stageName);
        const startTime = zonedDateTimeValue(
          stageData.start_time,
          `${stageName}.start_time`,
        );
        const endTime = zonedDateTimeValue(
          stageData.end_time,
          `${stageName}.end_time`,
        );
        const start = Date.parse(startTime);
        const end = Date.parse(endTime);
        if (end <= start) {
          throw new Error(`${stageName}.end_time 必须晚于 start_time`);
        }
        if (start < previousEnd) {
          throw new Error(`${name}.data.stages 必须按时间排列且不能重叠`);
        }
        if (end > Date.parse(observedAt)) {
          throw new Error(`${stageName}.end_time 不能晚于 observed_at`);
        }
        previousEnd = end;
        const stage = stringValue(stageData.stage, `${stageName}.stage`);
        if (
          ![
            'unknown',
            'awake',
            'sleeping',
            'out_of_bed',
            'awake_in_bed',
            'light',
            'deep',
            'rem',
          ].includes(stage)
        ) {
          throw new Error(`${stageName}.stage 无效`);
        }
        return {
          startTime,
          endTime,
          stage: stage as SleepStage['stage'],
        };
      });
      return {
        recordType,
        observedAt,
        sourcePackage,
        data: { stages },
      };
    }
    case 'oxygen_saturation': {
      const percentage = decimalNumberValue(
        data.percentage,
        `${name}.data.percentage`,
        0,
        true,
      );
      if (percentage > 100) {
        throw new Error(`${name}.data.percentage 必须不大于 100`);
      }
      return {
        recordType,
        observedAt,
        sourcePackage,
        data: { percentage },
      };
    }
    default:
      throw new Error(`${name}.record_type 无效`);
  }
}

export function parseHealthProfile(value: unknown): HealthProfile {
  const data = objectValue(value, 'health profile');
  if (!Array.isArray(data.health_facts)) {
    throw new Error('health profile.health_facts 必须是数组');
  }
  if (!Array.isArray(data.wearable_latest)) {
    throw new Error('health profile.wearable_latest 必须是数组');
  }

  const healthFacts = data.health_facts.map(parseHealthFact);
  const factIds = new Set<string>();
  for (const fact of healthFacts) {
    if (factIds.has(fact.id)) {
      throw new Error(`health profile.health_facts 包含重复的 ${fact.id}`);
    }
    factIds.add(fact.id);
  }

  const wearableLatest = data.wearable_latest.map(parseWearableLatest);
  const recordTypes = new Set<string>();
  for (const observation of wearableLatest) {
    if (recordTypes.has(observation.recordType)) {
      throw new Error(
        `health profile.wearable_latest 包含重复的 ${observation.recordType}`,
      );
    }
    recordTypes.add(observation.recordType);
  }

  return {
    personalProfile: parsePersonalProfile(data.personal_profile),
    healthFacts,
    wearableLatest,
  };
}

function parseHealthProfileCard(value: unknown, index: number): HealthProfileCard {
  const name = `health profile cards[${index}]`;
  const data = objectValue(value, name);
  const kind = stringValue(data.kind, `${name}.kind`);
  if (kind !== 'confirmation' && kind !== 'clarification') {
    throw new Error(`${name}.kind 无效`);
  }
  const targetType = stringValue(data.target_type, `${name}.target_type`);
  if (targetType !== 'personal_profile' && targetType !== 'health_fact') {
    throw new Error(`${name}.target_type 无效`);
  }
  const operation = stringValue(data.operation, `${name}.operation`);
  if (!['set', 'clear', 'add', 'update', 'retract'].includes(operation)) {
    throw new Error(`${name}.operation 无效`);
  }

  const proposedValue =
    data.proposed_value === null
      ? null
      : objectValue(data.proposed_value, `${name}.proposed_value`);

  if (!Array.isArray(data.options)) {
    throw new Error(`${name}.options 必须是数组`);
  }
  const optionIds = new Set<string>();
  const options = data.options.map((value, optionIndex) => {
    const optionName = `${name}.options[${optionIndex}]`;
    const option = objectValue(value, optionName);
    const id = stringValue(option.id, `${optionName}.id`);
    if (optionIds.has(id)) throw new Error(`${name}.options 包含重复的 ${id}`);
    optionIds.add(id);
    return {
      id,
      label: stringValue(option.label, `${optionName}.label`),
    };
  });

  return {
    id: uuidValue(data.id, `${name}.id`),
    kind,
    targetType,
    fieldName: stringValue(data.field_name, `${name}.field_name`),
    operation: operation as HealthProfileCard['operation'],
    question: stringValue(data.question, `${name}.question`),
    proposedValue,
    options,
    allowCustomInput: booleanValue(
      data.allow_custom_input,
      `${name}.allow_custom_input`,
    ),
    customInputPlaceholder: nullableStringValue(
      data.custom_input_placeholder,
      `${name}.custom_input_placeholder`,
    ),
    createdAt: dateValue(data.created_at, `${name}.created_at`),
  };
}

export function parseWearableImportResponse(
  value: unknown,
): WearableImportResponse {
  const data = objectValue(value, 'wearable import');
  const status = stringValue(data.status, 'wearable import.status');
  if (status !== 'completed') {
    throw new Error(`未知手环导入状态：${status}`);
  }
  return {
    importId: uuidValue(data.import_id, 'wearable import.import_id'),
    status,
    recordsCreated: integerValue(
      data.records_created,
      'wearable import.records_created',
    ),
    recordsUpdated: integerValue(
      data.records_updated,
      'wearable import.records_updated',
    ),
    recordsUnchanged: integerValue(
      data.records_unchanged,
      'wearable import.records_unchanged',
    ),
    recordsDeleted: integerValue(
      data.records_deleted,
      'wearable import.records_deleted',
    ),
  };
}

export function parseHealthProfileCards(value: unknown): HealthProfileCards {
  const data = objectValue(value, 'health profile cards');
  if (!Array.isArray(data.cards)) {
    throw new Error('health profile cards.cards 必须是数组');
  }
  return {
    cards: data.cards.map(parseHealthProfileCard),
  };
}

export function parseHealthProfileCardDecision(
  value: unknown,
): HealthProfileCardDecision {
  const data = objectValue(value, 'health profile card decision');
  const status = stringValue(data.status, 'health profile card decision.status');
  if (!['applied', 'rejected', 'conflicted'].includes(status)) {
    throw new Error(`未知健康档案卡片状态：${status}`);
  }
  return {
    cardId: uuidValue(data.card_id, 'health profile card decision.card_id'),
    status: status as HealthProfileCardDecision['status'],
  };
}

export function parseHealthProfileChangeResult(
  value: unknown,
): HealthProfileChangeResult {
  const data = objectValue(value, 'health profile change');
  const status = stringValue(data.status, 'health profile change.status');
  if (status !== 'applied') {
    throw new Error(`未知健康档案修改状态：${status}`);
  }
  const targetType = stringValue(
    data.target_type,
    'health profile change.target_type',
  );
  if (targetType !== 'personal_profile' && targetType !== 'health_fact') {
    throw new Error('health profile change.target_type 无效');
  }
  const operation = stringValue(
    data.operation,
    'health profile change.operation',
  );
  if (!['set', 'clear', 'add', 'update', 'retract'].includes(operation)) {
    throw new Error('health profile change.operation 无效');
  }
  return {
    changeId: uuidValue(data.change_id, 'health profile change.change_id'),
    status,
    targetType,
    fieldName: stringValue(data.field_name, 'health profile change.field_name'),
    operation: operation as HealthProfileChangeResult['operation'],
    targetId:
      data.target_id === null
        ? null
        : uuidValue(data.target_id, 'health profile change.target_id'),
    resultRevision: integerValue(
      data.result_revision,
      'health profile change.result_revision',
    ),
  };
}

export function parseChatStreamEvent(value: unknown): ChatStreamEvent {
  const data = objectValue(value, 'stream event');
  const type = stringValue(data.type, 'stream event.type');

  switch (type) {
    case 'message_started':
      return {
        type,
        userMessageId: uuidValue(data.user_message_id, 'user_message_id'),
        assistantMessageId: uuidValue(
          data.assistant_message_id,
          'assistant_message_id',
        ),
        runId: uuidValue(data.run_id, 'run_id'),
      };
    case 'text_delta':
      return {
        type,
        delta: stringValue(data.delta, 'delta'),
      };
    case 'agent_activity': {
      const phase = stringValue(data.phase, 'phase');
      if (
        !['thinking', 'searching', 'reading', 'organizing'].includes(phase)
      ) {
        throw new Error(`未知活动阶段：${phase}`);
      }
      return { type, phase: phase as AgentActivityPhase };
    }
    case 'message_completed':
      return {
        type,
        inputTokens: integerValue(data.input_tokens, 'input_tokens'),
        outputTokens: integerValue(data.output_tokens, 'output_tokens'),
        sources: parseSources(data.sources, 'sources'),
      };
    case 'message_failed': {
      const code = stringValue(data.code, 'code');
      if (code !== 'agent_run_failed') throw new Error(`未知失败代码：${code}`);
      return {
        type,
        code,
        errorType: stringValue(data.error_type, 'error_type'),
      };
    }
    default:
      throw new Error(`未知流事件：${type}`);
  }
}

export function readErrorPayload(value: unknown): {
  code?: string;
  message?: string;
} {
  const data = objectValue(value, 'error response');
  const detail =
    typeof data.detail === 'object' &&
    data.detail !== null &&
    !Array.isArray(data.detail)
      ? (data.detail as JsonObject)
      : null;
  return {
    code:
      typeof data.code === 'string'
        ? data.code
        : typeof detail?.code === 'string'
          ? detail.code
          : undefined,
    message:
      typeof data.message === 'string'
        ? data.message
        : typeof data.detail === 'string'
          ? data.detail
          : typeof detail?.message === 'string'
            ? detail.message
            : undefined,
  };
}
