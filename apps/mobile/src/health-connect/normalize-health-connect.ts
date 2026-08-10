import type { WearableRecord } from '../api/types';
import {
  type SupportedHealthConnectRecordType,
  ZEPP_DATA_ORIGIN,
} from './health-connect-types';

type JsonObject = Record<string, unknown>;

const sleepStages = {
  0: 'unknown',
  1: 'awake',
  2: 'sleeping',
  3: 'out_of_bed',
  4: 'light',
  5: 'deep',
  6: 'rem',
  7: 'awake_in_bed',
} as const;

function objectValue(value: unknown, name: string): JsonObject {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`${name} 必须是对象`);
  }
  return value as JsonObject;
}

function stringValue(value: unknown, name: string, maxLength?: number): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${name} 必须是非空字符串`);
  }
  if (maxLength !== undefined && value.length > maxLength) {
    throw new Error(`${name} 超过 ${maxLength} 个字符`);
  }
  return value;
}

function optionalString(value: unknown, name: string): string | null {
  if (value === null || value === undefined) return null;
  return stringValue(value, name, 255);
}

function numberValue(
  value: unknown,
  name: string,
  options: { integer?: boolean; minimum?: number; maximum?: number } = {},
): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`${name} 必须是有限数字`);
  }
  if (options.integer && !Number.isInteger(value)) {
    throw new Error(`${name} 必须是整数`);
  }
  if (options.minimum !== undefined && value < options.minimum) {
    throw new Error(`${name} 小于允许值`);
  }
  if (options.maximum !== undefined && value > options.maximum) {
    throw new Error(`${name} 大于允许值`);
  }
  return value;
}

function optionalInteger(value: unknown, name: string): number | null {
  if (value === null || value === undefined) return null;
  return numberValue(value, name, { integer: true, minimum: 0 });
}

function dateTimeValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  if (
    Number.isNaN(Date.parse(result)) ||
    !/(?:Z|[+-]\d{2}:\d{2})$/i.test(result)
  ) {
    throw new Error(`${name} 必须是带时区的日期时间`);
  }
  return result;
}

function zoneOffsetSeconds(value: unknown, name: string): number | null {
  if (value === null || value === undefined) return null;
  const offset = objectValue(value, name);
  return numberValue(offset.totalSeconds, `${name}.totalSeconds`, {
    integer: true,
    minimum: -64800,
    maximum: 64800,
  });
}

function metadata(value: unknown) {
  const data = objectValue(value, 'record.metadata');
  const dataOrigin = stringValue(
    data.dataOrigin,
    'record.metadata.dataOrigin',
    255,
  );
  if (dataOrigin !== ZEPP_DATA_ORIGIN) {
    throw new Error(`不支持 Health Connect 数据来源：${dataOrigin}`);
  }
  const deviceValue = data.device;
  let device: WearableRecord['device'];
  if (deviceValue === null || deviceValue === undefined) {
    device = null;
  } else {
    const nativeDevice = objectValue(deviceValue, 'record.metadata.device');
    device = {
      manufacturer: optionalString(
        nativeDevice.manufacturer,
        'record.metadata.device.manufacturer',
      ),
      model: optionalString(nativeDevice.model, 'record.metadata.device.model'),
      device_type: optionalInteger(
        nativeDevice.type,
        'record.metadata.device.type',
      ),
    };
  }

  return {
    external_record_id: stringValue(data.id, 'record.metadata.id', 255),
    source_package: dataOrigin,
    recording_method: optionalInteger(
      data.recordingMethod,
      'record.metadata.recordingMethod',
    ),
    device,
    source_last_modified_at: dateTimeValue(
      data.lastModifiedTime,
      'record.metadata.lastModifiedTime',
    ),
  };
}

function intervalBase(data: JsonObject) {
  return {
    ...metadata(data.metadata),
    start_time: dateTimeValue(data.startTime, 'record.startTime'),
    end_time: dateTimeValue(data.endTime, 'record.endTime'),
    start_zone_offset_seconds: zoneOffsetSeconds(
      data.startZoneOffset,
      'record.startZoneOffset',
    ),
    end_zone_offset_seconds: zoneOffsetSeconds(
      data.endZoneOffset,
      'record.endZoneOffset',
    ),
  };
}

function instantBase(data: JsonObject) {
  const time = dateTimeValue(data.time, 'record.time');
  const offset = zoneOffsetSeconds(data.zoneOffset, 'record.zoneOffset');
  return {
    ...metadata(data.metadata),
    start_time: time,
    end_time: time,
    start_zone_offset_seconds: offset,
    end_zone_offset_seconds: offset,
  };
}

/**
 * Converts only the ten approved Health Connect types into the strict server API.
 * Exercise routes/notes and sleep titles/notes are deliberately never copied.
 */
export function normalizeHealthConnectRecord(
  recordType: SupportedHealthConnectRecordType,
  value: unknown,
): WearableRecord {
  const data = objectValue(value, `Health Connect ${recordType}`);
  if (data.recordType !== undefined && data.recordType !== recordType) {
    throw new Error(
      `Health Connect 返回类型 ${String(data.recordType)}，预期 ${recordType}`,
    );
  }

  switch (recordType) {
    case 'Steps':
      return {
        ...intervalBase(data),
        record_type: 'steps',
        data: {
          count: numberValue(data.count, 'record.count', {
            integer: true,
            minimum: 0,
          }),
        },
      };
    case 'ExerciseSession':
      return {
        ...intervalBase(data),
        record_type: 'exercise',
        data: {
          exercise_type: numberValue(data.exerciseType, 'record.exerciseType', {
            integer: true,
            minimum: 0,
          }),
          title: optionalString(data.title, 'record.title'),
        },
      };
    case 'Distance':
      return {
        ...intervalBase(data),
        record_type: 'distance',
        data: {
          meters: numberValue(
            objectValue(data.distance, 'record.distance').inMeters,
            'record.distance.inMeters',
            { minimum: 0 },
          ),
        },
      };
    case 'ElevationGained':
      return {
        ...intervalBase(data),
        record_type: 'elevation_gained',
        data: {
          meters: numberValue(
            objectValue(data.elevation, 'record.elevation').inMeters,
            'record.elevation.inMeters',
            { minimum: 0 },
          ),
        },
      };
    case 'Weight':
      return {
        ...instantBase(data),
        record_type: 'weight',
        data: {
          kilograms: numberValue(
            objectValue(data.weight, 'record.weight').inKilograms,
            'record.weight.inKilograms',
            { minimum: Number.MIN_VALUE },
          ),
        },
      };
    case 'RespiratoryRate':
      return {
        ...instantBase(data),
        record_type: 'respiratory_rate',
        data: {
          breaths_per_minute: numberValue(data.rate, 'record.rate', {
            minimum: Number.MIN_VALUE,
          }),
        },
      };
    case 'RestingHeartRate':
      return {
        ...instantBase(data),
        record_type: 'resting_heart_rate',
        data: {
          beats_per_minute: numberValue(
            data.beatsPerMinute,
            'record.beatsPerMinute',
            { integer: true, minimum: 1 },
          ),
        },
      };
    case 'HeartRate': {
      if (!Array.isArray(data.samples) || data.samples.length === 0) {
        throw new Error('record.samples 必须是非空数组');
      }
      if (data.samples.length > 10000) {
        throw new Error('record.samples 超过 10000 条');
      }
      const samples = data.samples
        .map((sample, index) => {
          const item = objectValue(sample, `record.samples[${index}]`);
          return {
            time: dateTimeValue(item.time, `record.samples[${index}].time`),
            beats_per_minute: numberValue(
              item.beatsPerMinute,
              `record.samples[${index}].beatsPerMinute`,
              { integer: true, minimum: 1 },
            ),
          };
        })
        .sort((left, right) => left.time.localeCompare(right.time));
      if (new Set(samples.map((sample) => sample.time)).size !== samples.length) {
        throw new Error('record.samples 包含重复时间');
      }
      return {
        ...intervalBase(data),
        record_type: 'heart_rate',
        data: { samples },
      };
    }
    case 'SleepSession': {
      const nativeStages = data.stages ?? [];
      if (!Array.isArray(nativeStages)) {
        throw new Error('record.stages 必须是数组');
      }
      if (nativeStages.length > 10000) {
        throw new Error('record.stages 超过 10000 条');
      }
      const stages = nativeStages
        .map((stage, index) => {
          const item = objectValue(stage, `record.stages[${index}]`);
          const stageNumber = numberValue(
            item.stage,
            `record.stages[${index}].stage`,
            { integer: true, minimum: 0 },
          );
          const normalizedStage =
            sleepStages[stageNumber as keyof typeof sleepStages];
          if (normalizedStage === undefined) {
            throw new Error(`未知睡眠阶段：${stageNumber}`);
          }
          return {
            start_time: dateTimeValue(
              item.startTime,
              `record.stages[${index}].startTime`,
            ),
            end_time: dateTimeValue(
              item.endTime,
              `record.stages[${index}].endTime`,
            ),
            stage: normalizedStage,
          };
        })
        .sort((left, right) => left.start_time.localeCompare(right.start_time));
      return {
        ...intervalBase(data),
        record_type: 'sleep',
        data: { stages },
      };
    }
    case 'OxygenSaturation':
      return {
        ...instantBase(data),
        record_type: 'oxygen_saturation',
        data: {
          percentage: numberValue(data.percentage, 'record.percentage', {
            minimum: 0,
            maximum: 100,
          }),
        },
      };
  }
}
