<script lang="ts" setup>
import { computed } from "vue"
import { HistoryListType } from "../../type"

const emits = defineEmits<{
    (event:'delete'):void
    (event:'select'):void
}>();

const props = defineProps<{
    item:HistoryListType
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
          {{ props.item.name || '未命名会话' }}
        </div>
        <div class="subtitle" :class="{ danger: riskHint }">
          <span v-if="riskHint" class="risk-dot"></span>
          <span>{{ riskHint || formattedTime }}</span>
          <span v-if="riskHint"> · {{ formattedTime }}</span>
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
  border-radius: 9px;
  padding: 12px 14px;
  cursor: pointer;
  transition: background 0.2s ease, border-color 0.2s ease;
  margin-bottom: 6px;

  &:hover {
    background: #f0eee7;
    border-color: #e4dfd3;
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

    .title {
      font-size: 16px;
      font-weight: 700;
      color: #151515;
      margin-bottom: 8px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .subtitle {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 13px;
      color: #8a8174;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;

      &.danger {
        color: #d56b35;
      }
    }

    .risk-dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: #d56b35;
      flex-shrink: 0;
    }
  }

  .actions {
    opacity: 0;
    transition: opacity 0.2s ease;

    .delete-icon {
      color: #9b9488;
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
        color: #b4532b;
      }
    }
  }

  &:hover .actions {
    opacity: 1;
  }
}

// 激活状态
.history-card.active {
  border-color: transparent;
  background-color: #e3f1e9;

  .content .title {
    color: #0f6b49;
  }
}

// 响应式设计
@media (max-width: 480px) {
  .history-card {
    .content {
      .title {
        font-size: 14px;
      }

      .subtitle {
        font-size: 12px;
      }
    }
  }
}
</style>
