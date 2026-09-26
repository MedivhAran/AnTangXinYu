import { activityFamily, buildSleepSessions, toIso } from './GadgetbridgeService';

/**
 * 回归：华为导出的睡眠表时间戳是毫秒，活动表是秒。
 * 早先实现沿用活动表的换算系数，把 60 秒的采样间隔当成 60000 秒，
 * 于是每个采样点都被切成一次独立睡眠。
 */
describe('buildSleepSessions', () => {
  const START_MS = 1788633900000; // 2026-09-05T18:45:00Z
  const MINUTE_MS = 60 * 1000;

  function stageRun(startMs: number, minutes: number, stage: number) {
    return Array.from({ length: minutes }, (_, index) => ({
      TIMESTAMP: startMs + index * MINUTE_MS,
      STAGE: stage,
    }));
  }

  const statsRows = [
    {
      TIMESTAMP: START_MS + 27 * MINUTE_MS,
      BED_TIME: START_MS,
      SLEEP_SCORE: 86,
      DEEP_PART: 78,
      SLEEP_EFFICIENCY: 92,
      MIN_OXYGEN_SATURATION: 90,
      MIN_HEART_RATE: 53,
      MAX_HEART_RATE: 70,
    },
  ];

  it('毫秒时间戳合并成一晚，而不是每个采样点一晚', () => {
    const sessions = buildSleepSessions(
      [...stageRun(START_MS, 3, 4), ...stageRun(START_MS + 3 * MINUTE_MS, 30, 1), ...stageRun(START_MS + 33 * MINUTE_MS, 5, 2)],
      statsRows,
      1000,
      1000,
    );

    expect(sessions).toHaveLength(1);
    const [session] = sessions;
    expect(session.startTime).toBe(toIso(START_MS));
    // 最后一分钟采样代表 [t, t+60s)
    expect(session.endTime).toBe(toIso(START_MS + 38 * MINUTE_MS));
    expect(session.stages).toEqual([
      { startTime: toIso(START_MS), endTime: toIso(START_MS + 3 * MINUTE_MS), stage: 'awake' },
      {
        startTime: toIso(START_MS + 3 * MINUTE_MS),
        endTime: toIso(START_MS + 33 * MINUTE_MS),
        stage: 'light',
      },
      {
        startTime: toIso(START_MS + 33 * MINUTE_MS),
        endTime: toIso(START_MS + 38 * MINUTE_MS),
        stage: 'deep',
      },
    ]);
  });

  it('用同一夜的统计行补上评分、深睡时长与最低血氧', () => {
    const sessions = buildSleepSessions(stageRun(START_MS, 40, 1), statsRows, 1000, 1000);

    expect(sessions).toHaveLength(1);
    expect(sessions[0].score).toBe(86);
    expect(sessions[0].deepMinutes).toBe(78);
    expect(sessions[0].efficiency).toBe(92);
    expect(sessions[0].minOxygenSaturation).toBe(90);
    expect(sessions[0].minHeartRate).toBe(53);
    expect(sessions[0].maxHeartRate).toBe(70);
  });

  it('统计行缺失时评分等字段为 null，不影响阶段本身', () => {
    const sessions = buildSleepSessions(stageRun(START_MS, 40, 1), [], 1000, 1);

    expect(sessions).toHaveLength(1);
    expect(sessions[0].score).toBeNull();
    expect(sessions[0].deepMinutes).toBeNull();
    expect(sessions[0].stages).toHaveLength(1);
  });

  it('秒级时间戳（stageScale=1）得到与毫秒级相同的结果', () => {
    const startSeconds = Math.floor(START_MS / 1000);
    const secondRows = Array.from({ length: 40 }, (_, index) => ({
      TIMESTAMP: startSeconds + index * 60,
      STAGE: 1,
    }));
    const statsInSeconds = [{ ...statsRows[0], TIMESTAMP: startSeconds, BED_TIME: startSeconds }];

    const [millisecondSession] = buildSleepSessions(stageRun(START_MS, 40, 1), statsRows, 1000, 1000);
    const [secondSession] = buildSleepSessions(secondRows, statsInSeconds, 1, 1);

    expect(secondSession).toEqual(millisecondSession);
  });

  it('间隔超过 30 分钟才断成两晚', () => {
    const sessions = buildSleepSessions(
      [...stageRun(START_MS, 30, 1), ...stageRun(START_MS + 90 * MINUTE_MS, 30, 1)],
      [],
      1000,
      1,
    );

    expect(sessions).toHaveLength(2);
    expect(sessions[0].endTime).toBe(toIso(START_MS + 30 * MINUTE_MS));
    expect(sessions[1].startTime).toBe(toIso(START_MS + 90 * MINUTE_MS));
  });
});

describe('activityFamily', () => {
  it('取活动表的厂商前缀', () => {
    expect(activityFamily('HUAWEI_ACTIVITY_SAMPLE')).toBe('HUAWEI_');
    expect(activityFamily('MI_BAND_ACTIVITY_SAMPLE')).toBe('MI_BAND_');
  });

  it('表名不符合命名习惯时返回 null', () => {
    expect(activityFamily('HUAMI_EXTENDED_ACTIVITY_SAMPLE')).toBe('HUAMI_EXTENDED_');
    expect(activityFamily('DEVICE')).toBeNull();
    expect(activityFamily('HUAWEI_SLEEP_STAGE_SAMPLE')).toBeNull();
  });
});
