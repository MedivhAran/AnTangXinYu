<template>
  <div class="default-page">
    <div class="panel">
      <div class="brand-mark">糖</div>
      <div class="title">安糖心语</div>
      <div class="desc">陪你走过每一天</div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.default-page {
  box-sizing: border-box;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  box-shadow: var(--sh-2);
}

.panel {
  max-width: 360px;
  padding: 32px 36px;
  border-radius: var(--r-lg);
  background: var(--card-tint);
  border: 1px solid var(--border);
  box-shadow: var(--sh-1);
  text-align: center;
}

.brand-mark {
  width: 48px;
  height: 48px;
  margin: 0 auto 16px;
  border-radius: 14px;
  background: linear-gradient(135deg, #FF8E70 0%, #FFB07A 100%);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  font-weight: 800;
  box-shadow: 0 6px 14px -4px rgba(255, 126, 95, .55), inset 0 1px 0 rgba(255,255,255,.4);
}

.title {
  font-size: 22px;
  font-weight: 700;
  color: var(--ink);
  margin-bottom: 8px;
}

.desc {
  font-size: 14px;
  line-height: 1.6;
  color: var(--ink-3);
}
</style>
