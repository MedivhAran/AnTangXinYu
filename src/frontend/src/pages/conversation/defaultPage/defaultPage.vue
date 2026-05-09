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
  background: #f4f2ea;
}

.panel {
  max-width: 360px;
  padding: 32px 36px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #ddd8cd;
  box-shadow: 0 12px 28px rgba(83, 72, 55, 0.06);
  text-align: center;
}

.brand-mark {
  width: 44px;
  height: 44px;
  margin: 0 auto 16px;
  border-radius: 12px;
  background: #4f9d73;
  color: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: 800;
}

.title {
  font-size: 24px;
  font-weight: 700;
  color: #151515;
  margin-bottom: 10px;
}

.desc {
  font-size: 15px;
  line-height: 1.6;
  color: #8a8174;
}
</style>
