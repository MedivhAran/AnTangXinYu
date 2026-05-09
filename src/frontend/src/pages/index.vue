<script setup lang="ts">
import { onMounted } from "vue"

import { getUserInfoAPI } from "../apis/auth"
import { useUserStore } from "../store/user"

const userStore = useUserStore()

onMounted(async () => {
  userStore.initUserState()
  if (userStore.isLoggedIn && userStore.userInfo && !userStore.userInfo.avatar) {
    try {
      const response = await getUserInfoAPI(userStore.userInfo.id)
      if (response.data.status_code === 200 && response.data.data) {
        const userData = response.data.data
        userStore.updateUserInfo({
          avatar: userData.user_avatar || userData.avatar || "/src/assets/user.svg",
          description: userData.user_description || userData.description,
        })
      }
    } catch (error) {
      console.error("初始化时获取用户信息失败:", error)
    }
  }
})
</script>

<template>
  <div class="layout-shell">
    <div class="main-shell">
      <section class="content-shell">
        <router-view />
      </section>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.layout-shell {
  height: 100vh;
  overflow: hidden;
  background: #f4f2ea;
  color: #0f172a;
}

.main-shell {
  display: flex;
  height: 100vh;
  min-height: 0;
  overflow: hidden;
}

.content-shell {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

</style>
