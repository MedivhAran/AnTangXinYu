<script lang="ts" setup>
import { computed } from "vue"
import { HistoryListType } from "../../type"

const emits = defineEmits<{
    (event:'delete'):void
    (event:'select'):void
}>();

const props = defineProps<{
    item:HistoryListType
    unread?: boolean
}>();

// 格式化时间显示
const formattedTime = computed(() => {
  try {
    const date = new Date(props.item.createTime)
    const now = new Date()
    const diffInHours = (now.getTime() - date.getTime()) / (1000 * 60 * 60)
    
    if (diffInHours < 1) {
      return '刚刚'
    } else if (diffInHours < 24) {
      return `${Math.floor(diffInHours)}小时前`
    } else if (diffInHours < 24 * 7) {
      return `${Math.floor(diffInHours / 24)}天前`
    } else {
      return date.toLocaleDateString('zh-CN', { 
        month: 'short', 
        day: 'numeric' 
      })
    }
  } catch (error) {
    return '未知时间'
  }
})

const riskHint = computed(() => {
  const title = props.item.name || ''
  if (/低血糖|紧急|害怕|救我|难受/.test(title)) {
    return '高风险'
  }
  return ''
})

const deleteCard = (event: Event) => {
  event.stopPropagation()
  emits('delete')
}

// 选择会话
const selectCard = () => {
  emits('select')
}
</script>

<template>
  <div class="history-card" @click="selectCard">
    <div class="card-main">
      <div class="content">
        <div class="title" :title="props.item.name">
          <span v-if="props.unread" class="unread-dot" title="有新的主动提醒"></span>{{ props.item.name || '未命名会话' }}
        </div>
        <div class="subtitle" :class="{ danger: riskHint }">
          <span v-if="riskHint" class="risk-pill">
            <span class="risk-dot"></span>{{ riskHint }}
          </span>
          <span>{{ formattedTime }}</span>
        </div>
      </div>

      <div class="actions">
        <span
          class="delete-icon"
          @click="deleteCard"
        >×</span>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.history-card {
  position: relative;
  background-color: transparent;
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  padding: 10px 12px;
  cursor: pointer;
  transition: background 0.12s ease;
  margin-bottom: 2px;

  &:hover {
    background: rgba(74, 47, 25, .04);
  }

  .card-main {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
  }

  .content {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;

    .title {
      font-size: 13.5px;
      font-weight: 500;
      color: var(--ink);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .unread-dot {
      display: inline-block;
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--coral-deep, #FF6F4E);
      margin-right: 6px;
      vertical-align: middle;
      flex-shrink: 0;
    }

    .subtitle {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      color: var(--ink-3);
      font-family: var(--font-num);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;

      &.danger .risk-pill {
        background: var(--risk-bg);
        color: var(--risk-text);
      }
    }

    .risk-pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 1px 8px;
      border-radius: var(--r-pill);
      font-size: 10.5px;
      font-weight: 600;
      line-height: 1.6;
      background: var(--risk-bg);
      color: var(--risk-text);
    }

    .risk-dot {
      width: 5px;
      height: 5px;
      border-radius: 999px;
      background: currentColor;
    }
  }

  .actions {
    opacity: 0;
    transition: opacity 0.2s ease;

    .delete-icon {
      color: var(--ink-4);
      font-size: 18px;
      font-weight: bold;
      cursor: pointer;
      width: 20px;
      height: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: color 0.2s;

      &:hover {
        color: var(--coral-deep);
      }
    }
  }

  &:hover .actions {
    opacity: 1;
  }
}

// 激活状态：奶油 → 浅珊瑚的渐变
.history-card.active {
  background: linear-gradient(90deg, #FFEDE5 0%, #FFF6EF 100%);

  .content .title {
    color: var(--coral-deep);
    font-weight: 600;
  }
}

@media (max-width: 480px) {
  .history-card {
    .content {
      .title { font-size: 13px; }
      .subtitle { font-size: 11px; }
    }
  }
}
</style>
