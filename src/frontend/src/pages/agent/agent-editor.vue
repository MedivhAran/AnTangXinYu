<script setup lang="ts">
import { ref, reactive, onMounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { 
  Edit, 
  Plus, 
  Delete, 
  VideoPlay, 
  Check, 
  ArrowDown, 
  ArrowRight,
  ArrowLeft,
  ChatDotRound,
  Setting,
  DocumentCopy
} from '@element-plus/icons-vue'
import type { UploadProps, UploadUserFile } from 'element-plus'
import { createAgentAPI, updateAgentAPI, getAgentByIdAPI } from '../../apis/agent'
import { getVisibleLLMsAPI, getAgentModelsAPI, type LLMResponse } from '../../apis/llm'
import { getVisibleToolsAPI, type ToolResponse } from '../../apis/tool'
import { Agent, AgentFormData } from '../../type'
import { uploadFileAPI } from '../../apis/file'

const route = useRoute()
const router = useRouter()

const emit = defineEmits<{
  update: []
}>()

// 响应式数据
const loading = ref(false)
const formRef = ref()
const isEditing = ref(false)
const editingAgentId = ref('')
const fileList = ref<UploadUserFile[]>([])


// 智能体表单数据
// 注：mcp_ids / agent_skill_ids 仍是 Agent 模型的字段（后端持久化），
// 但 UI 不再让用户编辑，由后端 _ensure_antang_agent 自动绑定。
const formData = reactive<AgentFormData>({
  name: '',
  description: '',
  logo_url: '',
  tool_ids: [],
  llm_id: '',
  mcp_ids: [],
  system_prompt: '',
  agent_skill_ids: [],
})

// 折叠面板状态
const collapseItems = ref({
  basic: false,
  aiModel: true,
  tools: false,
})

// 选项数据
const llmOptions = ref<Array<LLMResponse & { name: string }>>([])
const toolOptions = ref<Array<ToolResponse & { name: string; icon: string }>>([])

// 数据加载状态
const dataLoading = ref({
  llm: false,
  tool: false,
})

// 表单验证规则
const rules = {
  name: [{ required: true, message: '请输入智能体名称', trigger: 'blur' }],
  description: [{ required: true, message: '请输入智能体描述', trigger: 'blur' }],
  system_prompt: [{ required: true, message: '请输入系统提示词', trigger: 'blur' }],
  llm_id: [{ required: true, message: '请选择大模型', trigger: 'change' }]
}





// 方法
const loadAgent = (agent?: Agent) => {
  if (agent) {
    isEditing.value = true
    editingAgentId.value = agent.agent_id

    const processedToolIds = Array.isArray(agent.tool_ids)
      ? agent.tool_ids.filter(id => id)
      : []
    // mcp_ids / agent_skill_ids 透传保存，UI 不暴露编辑入口。
    const processedMcpIds = Array.isArray(agent.mcp_ids) ? agent.mcp_ids.filter(id => id) : []
    const processedAgentSkillIds = Array.isArray(agent.agent_skill_ids) ? agent.agent_skill_ids.filter(id => id) : []

    Object.assign(formData, {
      name: agent.name || '',
      description: agent.description || '',
      logo_url: agent.logo_url || '',
      tool_ids: processedToolIds,
      llm_id: agent.llm_id || '',
      mcp_ids: processedMcpIds,
      system_prompt: agent.system_prompt || '',
      agent_skill_ids: processedAgentSkillIds,
    })

    setTimeout(() => {
      validateIdMatching()
    }, 100)

    if (agent.logo_url) {
      fileList.value = [{
        name: 'avatar',
        url: agent.logo_url
      }]
    } else {
      fileList.value = []
    }
  } else {
    isEditing.value = false
    editingAgentId.value = ''

    Object.assign(formData, {
      name: '',
      description: '',
      logo_url: '',
      tool_ids: [],
      llm_id: '',
      mcp_ids: [],
      system_prompt: '',
      agent_skill_ids: [],
    })
    fileList.value = []
  }
}

// 切换折叠面板
const toggleCollapse = (key: keyof typeof collapseItems.value) => {
  collapseItems.value[key] = !collapseItems.value[key]
}



// 上传相关
const uploadLoading = ref(false)

const handleFileChange: UploadProps['onChange'] = async (uploadFile) => {
  if (uploadFile.raw) {
    const file = uploadFile.raw
    // 文件大小和类型检查
    const isLt2M = file.size / 1024 / 1024 < 2
    if (!isLt2M) {
      ElMessage.error('上传头像图片大小不能超过 2MB!')
      return
    }
    const isJpgOrPng = file.type === 'image/jpeg' || file.type === 'image/png'
    if (!isJpgOrPng) {
      ElMessage.error('上传头像图片只能是 JPG/PNG 格式!')
      return
    }
    
    // 开始上传
    uploadLoading.value = true
    try {
      const uploadFormData = new FormData()
      uploadFormData.append('file', file)
      
      const response = await uploadFileAPI(uploadFormData)
      
      if (response.data.status_code === 200) {
        formData.logo_url = response.data.data
        ElMessage.success('头像上传成功')
      } else {
        ElMessage.error(response.data.status_message || '头像上传失败')
      }
    } catch (error) {
      console.error('头像上传失败:', error)
      ElMessage.error('头像上传失败')
    } finally {
      uploadLoading.value = false
    }
  }
}

const handleFileRemove: UploadProps['onRemove'] = () => {
  formData.logo_url = ''
}

// 保存智能体
const saveAgent = async () => {
  try {
    // 表单验证
    const valid = await formRef.value?.validate()
    if (!valid) {
      ElMessage.warning('请完善必填信息后再提交')
      return
    }
    
    loading.value = true
    
    // 构建请求数据，确保字段正确
    const requestData = {
      name: formData.name,
      description: formData.description,
      logo_url: formData.logo_url,
      tool_ids: formData.tool_ids,
      llm_id: formData.llm_id,
      mcp_ids: formData.mcp_ids,
      system_prompt: formData.system_prompt,
      agent_skill_ids: formData.agent_skill_ids,
    }
    
    if (isEditing.value) {
      // 确保agent_id字段存在
      if (!editingAgentId.value) {
        ElMessage.error('缺少智能体ID，无法更新')
        loading.value = false
        return
      }
      
      // 将agent_id添加到请求数据中
      const updateData = {
        agent_id: editingAgentId.value,
        ...requestData
      }
      
      console.log('更新智能体数据:', updateData)
      const response = await updateAgentAPI(updateData)
      
      if (response.data.status_code === 200) {
        ElMessage.success('智能体更新成功')
        // 保存成功后跳转到智能体列表页
        router.push('/agent')
      } else {
        ElMessage.error(response.data.status_message || '更新失败')
      }
    } else {
      console.log('创建智能体数据:', requestData)
      const response = await createAgentAPI(requestData)
      
      if (response.data.status_code === 200) {
        ElMessage.success('智能体创建成功')
        // 保存成功后跳转到智能体列表页
        router.push('/agent')
      } else {
        ElMessage.error(response.data.status_message || '创建失败')
      }
    }
  } catch (error: any) {
    console.error('操作失败:', error)
    if (error.response?.data?.status_message) {
      ElMessage.error(error.response.data.status_message)
    } else if (error.response?.data?.message) {
      ElMessage.error(error.response.data.message)
    } else {
      ElMessage.error(isEditing.value ? '智能体更新失败' : '智能体创建失败')
    }
  } finally {
    loading.value = false
  }
}





// 加载大模型数据
const loadLLMOptions = async () => {
  try {
    dataLoading.value.llm = true
    console.log('🔄 开始加载大模型数据...')
    
    // 优先使用智能体专用的大模型API
    let response
    try {
      response = await getAgentModelsAPI()
      console.log('📡 智能体大模型API响应:', response)
    } catch (error) {
      console.log('⚠️ 智能体大模型API失败，尝试使用通用API:', error)
      response = await getVisibleLLMsAPI()
      console.log('📡 通用大模型API响应:', response)
    }
    
    if (response.data.status_code === 200) {
      const rawData = response.data.data
      console.log('📦 原始大模型数据:', rawData)
      
      // 处理数据结构：可能是 Record<string, LLMResponse[]> 或直接的 LLMResponse[]
      let llmArray: LLMResponse[] = []
      
      if (Array.isArray(rawData)) {
        // 如果是直接数组（智能体API返回的）
        llmArray = rawData
      } else if (typeof rawData === 'object' && rawData !== null) {
        // 如果是对象（通用API返回的），提取LLM类型的模型
        if (rawData.LLM && Array.isArray(rawData.LLM)) {
          llmArray = rawData.LLM
        } else {
          // 如果没有LLM字段，尝试提取所有值并合并
          llmArray = Object.values(rawData).flat()
        }
      }
      
      console.log('🔄 处理后的数组:', llmArray)
      
      llmOptions.value = llmArray.map(llm => ({
        ...llm,
        name: `${llm.model} (${llm.provider})`
      }))
      
      console.log(`✅ 成功加载 ${llmOptions.value.length} 个大模型`)
      console.log('🧠 处理后的大模型数据:', llmOptions.value)
      // 新建模式下默认选第一个
      if (!isEditing.value && !formData.llm_id && llmOptions.value.length > 0) {
        formData.llm_id = llmOptions.value[0].llm_id
      }
    } else {
      console.error('❌ 大模型API返回错误:', response.data.status_message)
      ElMessage.error(`加载大模型失败: ${response.data.status_message}`)
    }
  } catch (error) {
    console.error('❌ 加载大模型失败:', error)
    ElMessage.error('加载大模型列表失败')
  } finally {
    dataLoading.value.llm = false
  }
}

// 加载工具数据
const loadToolOptions = async () => {
  try {
    dataLoading.value.tool = true
    console.log('🔄 开始加载工具数据...')
    
    const response = await getVisibleToolsAPI()
    console.log('📡 工具API响应:', response)
    
    if (response.data.status_code === 200) {
      const rawData = response.data.data
      console.log('📦 原始工具数据:', rawData)
      
      toolOptions.value = rawData.map(tool => ({
        ...tool,
        name: tool.display_name
      }))
      
      console.log(`✅ 成功加载 ${toolOptions.value.length} 个工具`)
      console.log('🔧 处理后的工具数据:', toolOptions.value)
    } else {
      console.error('❌ 工具API返回错误:', response.data.status_message)
    }
  } catch (error) {
    console.error('❌ 加载工具失败:', error)
    ElMessage.error('加载工具列表失败')
  } finally {
    dataLoading.value.tool = false
  }
}

// 验证ID匹配性（UI 已不再选 MCP/Skill，这里只校验 LLM / 工具）
const validateIdMatching = () => {
  if (formData.llm_id) {
    const llmExists = llmOptions.value.some(llm => llm.llm_id === formData.llm_id)
    if (!llmExists) {
      console.warn('⚠️ 大模型ID不匹配:', formData.llm_id, '可用选项:', llmOptions.value.map(l => l.llm_id))
    }
  }

  if (formData.tool_ids.length > 0) {
    const toolOptionsIds = toolOptions.value.map(t => t.tool_id)
    const unmatchedToolIds = formData.tool_ids.filter(id => !toolOptionsIds.includes(id))
    if (unmatchedToolIds.length > 0) {
      console.warn('⚠️ 工具ID不匹配:', unmatchedToolIds, '可用选项:', toolOptionsIds)
    }
  }
}

// 从API加载智能体数据
const loadAgentFromAPI = async (agentId: string) => {
  try {
    loading.value = true
    // ElMessage.info('正在加载智能体数据...')
    
    const response = await getAgentByIdAPI(agentId)
    if (response.data.status_code === 200 && response.data.data) {
      const agentData = response.data.data as any
      // console.log('🔍 API返回的智能体原始数据:', agentData)
      
      // 转换API数据为Agent类型，兼容 id 和 agent_id
      const agent: Agent = {
        agent_id: agentData.id || agentData.agent_id,
        name: agentData.name,
        description: agentData.description,
        logo_url: agentData.logo_url,
        tool_ids: agentData.tool_ids || [],
        llm_id: agentData.llm_id,
        mcp_ids: agentData.mcp_ids || [],
        system_prompt: agentData.system_prompt,
        agent_skill_ids: agentData.agent_skill_ids || [],
        created_time: new Date().toISOString()
      }
      
      // console.log('🔄 转换后的智能体数据:', agent)
      loadAgent(agent)
      ElMessage.success('智能体数据加载成功')
    } else {
      ElMessage.error(response.data.status_message || '智能体不存在')
      goBack()
    }
  } catch (error) {
    console.error('加载智能体失败:', error)
    ElMessage.error('加载智能体数据失败')
    goBack()
  } finally {
    loading.value = false
  }
}

// 返回智能体列表
const goBack = () => {
  router.push('/agent')
}

const initializeData = async () => {
  try {
    await Promise.all([
      loadLLMOptions(),
      loadToolOptions(),
    ])
  } catch (error) {
    console.error('数据初始化失败:', error)
  }
}

onMounted(async () => {
  console.log('📱 页面加载开始...')
  console.log('🔍 当前路由参数:', route.query)
  
  // 先加载选项数据，这是前提条件
  console.log('⏳ 正在加载选项数据...')
  await initializeData()
  console.log('✅ 选项数据加载完成')
  
  // 確保所有選項數據都加載完成後，再加載智能體數據
  const agentId = route.query.id as string
  if (agentId) {
    console.log('🔄 开始加载智能体数据，ID:', agentId, '类型:', typeof agentId)
    await loadAgentFromAPI(agentId)
  } else {
    console.log('🆕 创建新智能体模式')
    // 创建模式下，清空表单并设置默认值
    loadAgent()
    // 确保默认选中第一个模型
    if (!formData.llm_id && llmOptions.value.length > 0) {
      formData.llm_id = llmOptions.value[0].llm_id
    }
  }
  

})

defineExpose({ loadAgent })
</script>

<template>
  <div class="agent-editor">
    <!-- 顶部工具栏 -->
    <!-- 三栏布局主体 -->
    <div class="editor-body">
      <!-- 左侧：系统提示词编辑器 -->
      <div class="left-panel">
        <div class="panel-header">
          <div class="header-content">
            <el-icon class="panel-icon"><DocumentCopy /></el-icon>
            <span class="panel-title">智能体画像</span>
          </div>
        </div>
        
        <div class="panel-content">
          <!-- 基础信息 -->
          <div class="basic-info-section">
            <div class="basic-info-layout">
              <!-- 头像 -->
              <el-upload
                v-model:file-list="fileList"
                class="avatar-uploader"
                action="#"
                :show-file-list="false"
                :auto-upload="false"
                :on-change="handleFileChange"
                :on-remove="handleFileRemove"
              >
                <div class="avatar-wrapper">
                  <img v-if="formData.logo_url" :src="formData.logo_url" class="avatar" />
                  <div v-else class="avatar-placeholder">
                    <el-icon class="avatar-icon"><Plus /></el-icon>
                    <span class="avatar-text">上传</span>
                  </div>
                </div>
              </el-upload>

              <!-- 名称 + 描述 -->
              <div class="basic-info-fields">
                <div class="field-with-label">
                  <span class="field-label"><span class="required-mark">*</span>名称：</span>
                  <el-input v-model="formData.name" placeholder="" class="form-input name-input" />
                </div>
                <div class="field-with-label field-with-label--textarea">
                  <span class="field-label">描述：</span>
                  <el-input
                    v-model="formData.description"
                    type="textarea"
                    :rows="2"
                    placeholder=""
                    class="form-textarea"
                  />
                </div>
              </div>
            </div>
          </div>

          <!-- 系统提示词 -->
          <div class="prompt-editor-wrapper">
            <div class="prompt-label">系统提示词：</div>
            <el-input
              v-model="formData.system_prompt"
              type="textarea"
              :rows="13"
              placeholder="请输入系统提示词，定义智能体的角色、能力和行为规范..."
              class="prompt-editor"
            />
          </div>
        </div>
      </div>

      <!-- 中间：智能体配置 -->
      <div class="center-panel">
        <div class="panel-header">
          <div class="header-content">
            <el-icon class="panel-icon"><Setting /></el-icon>
            <span class="panel-title">基础配置</span>
          </div>
          <div class="panel-actions">
            <el-button @click="goBack" :disabled="loading" class="cancel-btn">取消</el-button>
            <el-button @click="saveAgent" type="primary" :loading="loading" :icon="Check" class="save-btn">
              {{ isEditing ? '保存更改' : '创建智能体' }}
            </el-button>
          </div>
        </div>
        
        <div class="panel-content">
          <el-form ref="formRef" :model="formData" :rules="rules" label-width="100px" class="config-form">
            <!-- AI模型 -->
            <div class="config-section">
              <div class="section-header" @click="toggleCollapse('aiModel')">
                <div class="section-title">
                  <el-icon class="section-icon">
                    <ArrowDown v-if="collapseItems.aiModel" />
                    <ArrowRight v-else />
                  </el-icon>
                  <span>AI模型</span>
                </div>
                <div class="section-badge">
                  <el-tag size="small" type="warning" effect="plain">核心</el-tag>
                </div>
              </div>
              <div v-show="collapseItems.aiModel" class="section-content">
                <el-form-item label="模型" prop="llm_id">
                  <el-select 
                    v-model="formData.llm_id" 
                    placeholder="🔍 搜索或选择大语言模型"
                    :loading="dataLoading.llm"
                    class="form-select"
                    filterable
                    clearable
                    reserve-keyword
                  >
                    <template #prefix>
                      <span v-if="dataLoading.llm" style="color: #1d4ed8; font-size: 12px; font-weight: 500;">加载中...</span>
                      <span v-else style="color: #1d4ed8; font-size: 12px; font-weight: 600;"><img src="/src/assets/model.svg" style="width:14px;height:14px;vertical-align:middle;margin-right:4px;" />{{ llmOptions.length }}个模型</span>
                    </template>
                    <el-option
                      v-for="llm in llmOptions"
                      :key="llm.llm_id"
                      :label="llm.name"
                      :value="llm.llm_id"
                    >
                      <div class="custom-option">
                        <img src="/src/assets/model.svg" class="option-logo" alt="AI Model" />
                        <span class="option-name">{{ llm.name }}</span>
                        <span class="option-badge ai-badge">AI</span>
                      </div>
                    </el-option>
                  </el-select>
                </el-form-item>

              </div>
            </div>

            <!-- 工具 -->
            <div class="config-section">
              <div class="section-header" @click="toggleCollapse('tools')">
                <div class="section-title">
                  <el-icon class="section-icon">
                    <ArrowDown v-if="collapseItems.tools" />
                    <ArrowRight v-else />
                  </el-icon>
                  <span>工具</span>
                </div>
                <div class="section-badge">
                </div>
              </div>
              <div v-show="collapseItems.tools" class="section-content">
                <el-form-item label="选择工具">
                  <el-select
                    v-model="formData.tool_ids"
                    multiple
                    placeholder="🔍 搜索或选择工具"
                    class="form-select"
                    :loading="dataLoading.tool"
                    filterable
                    clearable
                    collapse-tags
                    collapse-tags-tooltip
                    :max-collapse-tags="3"
                  >
                    <template #prefix>
                      <span v-if="dataLoading.tool" style="color: #c2410c; font-size: 12px; font-weight: 500;">加载中...</span>
                      <span v-else style="color: #c2410c; font-size: 12px; font-weight: 600;"><img src="/src/assets/plugin.svg" style="width:14px;height:14px;vertical-align:middle;margin-right:4px;" />{{ toolOptions.length }}个工具</span>
                    </template>
                    <el-option
                      v-for="tool in toolOptions"
                      :key="tool.tool_id"
                      :label="tool.name"
                      :value="tool.tool_id"
                    >
                      <div class="custom-option">
                        <img :src="tool.logo_url || '/src/assets/plugin.svg'" class="option-logo" :alt="tool.name" />
                        <span class="option-name">{{ tool.name }}</span>
                        <span class="option-badge tool-badge">TOOL</span>
                      </div>
                    </el-option>
                  </el-select>
                </el-form-item>
              </div>
            </div>

          </el-form>
        </div>
      </div>


    </div>
  </div>
</template>

<style lang="scss" scoped>
.agent-editor {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: linear-gradient(135deg, #f0f4ff 0%, #e0e7ff 50%, #f3f4f6 100%);
  position: relative;
  
  &::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 300px;
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #06b6d4 100%);
    opacity: 0.03;
    z-index: 0;
  }

  .editor-body {
    display: flex;
    flex: 1;
    overflow: hidden;
    gap: 0;
    padding: 0;
    position: relative;
    z-index: 5;

    .left-panel,
    .center-panel {
      display: flex;
      flex-direction: column;
      background: rgba(255, 255, 255, 0.9);
      backdrop-filter: blur(20px);
      border: none;
      border-right: 1px solid rgba(226, 232, 240, 0.4);
      border-radius: 0;
      box-shadow: none;
      overflow: hidden;
      position: relative;
      
      &::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 1px;
        background: linear-gradient(90deg, transparent, rgba(99, 102, 241, 0.3), transparent);
      }

      .panel-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 16px 24px;
        height: 64px;
        box-sizing: border-box;
        background: linear-gradient(135deg, rgba(248, 250, 252, 0.8) 0%, rgba(241, 245, 249, 0.8) 100%);
        border-bottom: 1px solid rgba(226, 232, 240, 0.3);
        position: relative;
        
        &::after {
          content: '';
          position: absolute;
          bottom: 0;
          left: 24px;
          right: 24px;
          height: 1px;
          background: linear-gradient(90deg, transparent, rgba(99, 102, 241, 0.2), transparent);
        }

        .header-content {
          display: flex;
          flex-direction: row;
          align-items: center;
          gap: 10px;

          .panel-icon {
            color: #6366f1;
            font-size: 20px;
            filter: drop-shadow(0 2px 4px rgba(99, 102, 241, 0.15));
            flex-shrink: 0;
          }

          .panel-title {
            font-size: 16px;
            font-weight: 700;
            color: #1e293b;
            letter-spacing: -0.025em;
          }
        }

        .panel-actions {
          display: flex;
          gap: 10px;
          align-items: center;

          .cancel-btn {
            border: 1px solid rgba(226, 232, 240, 0.8);
            color: #64748b;
            background: rgba(255, 255, 255, 0.9);
            transition: all 0.3s ease;
            border-radius: 10px;
            font-weight: 500;
            height: 34px;
            padding: 0 16px;
            font-size: 13px;

            &:hover {
              border-color: #6366f1;
              color: #6366f1;
            }
          }

          .save-btn {
            background: linear-gradient(135deg, #38bdf8 0%, #0ea5e9 100%);
            border: none;
            font-weight: 600;
            transition: all 0.3s ease;
            border-radius: 10px;
            height: 34px;
            padding: 0 18px;
            font-size: 13px;
            box-shadow: 0 4px 12px rgba(14, 165, 233, 0.3);

            &:hover {
              transform: translateY(-1px);
              box-shadow: 0 8px 20px rgba(14, 165, 233, 0.4);
            }
          }
        }
      }

      .panel-content {
        flex: 1;
        overflow-y: auto;
        padding: 28px;
        background: rgba(255, 255, 255, 0.02);
      }
    }

    .left-panel {
      width: 50%;

      .basic-info-section {
        margin-bottom: 20px;
        padding-bottom: 20px;
        border-bottom: 1px solid rgba(226, 232, 240, 0.4);

        .basic-info-layout {
          display: flex;
          gap: 16px;
          align-items: flex-start;

          .avatar-uploader {
            flex-shrink: 0;

            :deep(.el-upload) {
              border: 2px dashed rgba(99, 102, 241, 0.25);
              border-radius: 12px;
              cursor: pointer;
              overflow: hidden;
              transition: all 0.3s ease;
              width: 72px;
              height: 72px;
              display: flex;
              align-items: center;
              justify-content: center;
              background: linear-gradient(135deg, rgba(248, 250, 252, 0.9) 0%, rgba(241, 245, 249, 0.9) 100%);

              &:hover {
                border-color: #6366f1;
                background: rgba(239, 246, 255, 0.9);
                transform: scale(1.03);
              }
            }

            .avatar-wrapper {
              width: 72px;
              height: 72px;
              display: flex;
              align-items: center;
              justify-content: center;

              .avatar {
                width: 72px;
                height: 72px;
                object-fit: cover;
                border-radius: 10px;
              }

              .avatar-placeholder {
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 4px;

                .avatar-icon {
                  font-size: 20px;
                  color: #6366f1;
                }

                .avatar-text {
                  font-size: 11px;
                  color: #94a3b8;
                  font-weight: 500;
                }
              }
            }
          }

          .basic-info-fields {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 10px;

            .field-with-label {
              display: flex;
              align-items: center;
              gap: 8px;

              &--textarea {
                align-items: flex-start;

                .field-label {
                  margin-top: 8px;
                }
              }

              .field-label {
                font-size: 13px;
                font-weight: 600;
                color: #475569;
                white-space: nowrap;
                width: 36px;
                text-align: right;
                flex-shrink: 0;
                position: relative;

                .required-mark {
                  color: #ef4444;
                  position: absolute;
                  left: -10px;
                  top: 0;
                }
              }

              .form-input,
              .form-textarea {
                flex: 1;
              }
            }

            .name-input {
              :deep(.el-input__inner) {
                font-size: 15px;
                font-weight: 600;
                color: #1e293b;
              }
            }
          }
        }
      }

      .prompt-editor-wrapper {
        .prompt-label {
          font-size: 13px;
          font-weight: 600;
          color: #475569;
          margin-bottom: 8px;
        }

        .prompt-editor {
          :deep(.el-textarea__inner) {
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'SF Mono', monospace;
            line-height: 1.7;
            font-size: 14px;
            resize: none;
            border: 1px solid rgba(226, 232, 240, 0.5);
            border-radius: 16px;
            padding: 20px;
            background: rgba(248, 250, 252, 0.8);
            backdrop-filter: blur(10px);
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 
              0 2px 8px rgba(0, 0, 0, 0.02),
              inset 0 1px 0 rgba(255, 255, 255, 0.5);
            
            &:focus {
              border-color: #6366f1;
              background: rgba(255, 255, 255, 0.95);
              box-shadow: 
                0 0 0 4px rgba(99, 102, 241, 0.1),
                0 8px 24px rgba(99, 102, 241, 0.08),
                inset 0 1px 0 rgba(255, 255, 255, 0.8);
              transform: translateY(-1px);
            }
            
            &:hover {
              border-color: rgba(99, 102, 241, 0.3);
              background: rgba(255, 255, 255, 0.8);
            }
          }
        }

        .prompt-info {
          display: flex;
          justify-content: space-between;
          margin-top: 20px;
          padding: 16px 20px;
          background: linear-gradient(135deg, rgba(248, 250, 252, 0.8) 0%, rgba(241, 245, 249, 0.8) 100%);
          border-radius: 12px;
          border: 1px solid rgba(226, 232, 240, 0.4);
          backdrop-filter: blur(10px);
          box-shadow: 
            0 2px 8px rgba(0, 0, 0, 0.02),
            inset 0 1px 0 rgba(255, 255, 255, 0.6);

          .info-item {
            display: flex;
            align-items: center;
            gap: 8px;

            .info-label {
              font-size: 12px;
              color: #64748b;
              font-weight: 500;
            }

            .info-value {
              font-size: 14px;
              color: #1e293b;
              font-weight: 600;
            }
          }
        }
      }
    }

    .center-panel {
      width: 50%;

      .config-form {
        .config-section {
          margin-bottom: 24px;
          border: 1px solid rgba(226, 232, 240, 0.4);
          border-radius: 16px;
          overflow: hidden;
          transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
          background: rgba(255, 255, 255, 0.3);
          backdrop-filter: blur(10px);
          box-shadow: 
            0 2px 8px rgba(0, 0, 0, 0.02),
            inset 0 1px 0 rgba(255, 255, 255, 0.5);

          &:hover {
            border-color: rgba(99, 102, 241, 0.4);
            box-shadow: 
              0 8px 24px rgba(99, 102, 241, 0.08),
              0 2px 8px rgba(0, 0, 0, 0.04),
              inset 0 1px 0 rgba(255, 255, 255, 0.7);
            transform: translateY(-1px);
          }

          .section-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 22px 24px;
            background: linear-gradient(135deg, rgba(248, 250, 252, 0.6) 0%, rgba(241, 245, 249, 0.6) 100%);
            cursor: pointer;
            user-select: none;
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            
            &::after {
              content: '';
              position: absolute;
              bottom: 0;
              left: 20px;
              right: 20px;
              height: 1px;
              background: linear-gradient(90deg, transparent, rgba(99, 102, 241, 0.15), transparent);
            }

            &:hover {
              background: linear-gradient(135deg, rgba(239, 246, 255, 0.8) 0%, rgba(219, 234, 254, 0.8) 100%);
              
              &::after {
                background: linear-gradient(90deg, transparent, rgba(99, 102, 241, 0.3), transparent);
              }
            }

            .section-title {
              display: flex;
              align-items: center;
              gap: 12px;

              .section-icon {
                color: #6366f1;
                font-size: 18px;
                filter: drop-shadow(0 1px 2px rgba(99, 102, 241, 0.2));
                transition: all 0.3s ease;
              }

              span {
                font-weight: 700;
                color: #1e293b;
                font-size: 16px;
                letter-spacing: -0.025em;
              }
            }

            .section-badge {
              .badge {
                margin-left: auto;
              }
            }
          }

          .section-content {
            padding: 28px;
            background: rgba(255, 255, 255, 0.5);
            backdrop-filter: blur(5px);

            .el-form-item {
              margin-bottom: 20px;

              &:last-child {
                margin-bottom: 0;
              }
            }
          }
        }
      }

      .form-input,
      .form-textarea,
      .form-select {
        :deep(.el-input__wrapper) {
          border: 1px solid rgba(226, 232, 240, 0.5);
          border-radius: 12px;
          background: rgba(248, 250, 252, 0.8);
          backdrop-filter: blur(10px);
          transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
          box-shadow: 
            0 1px 3px rgba(0, 0, 0, 0.02),
            inset 0 1px 0 rgba(255, 255, 255, 0.5);
          min-height: 48px;

          &:hover {
            border-color: rgba(99, 102, 241, 0.4);
            background: rgba(255, 255, 255, 0.9);
            transform: translateY(-1px);
            box-shadow: 
              0 4px 12px rgba(99, 102, 241, 0.05),
              inset 0 1px 0 rgba(255, 255, 255, 0.7);
          }

          &.is-focus {
            border-color: #6366f1;
            background: rgba(255, 255, 255, 0.95);
            box-shadow: 
              0 0 0 4px rgba(99, 102, 241, 0.1),
              0 8px 24px rgba(99, 102, 241, 0.08),
              inset 0 1px 0 rgba(255, 255, 255, 0.8);
            transform: translateY(-2px);
          }
        }

        :deep(.el-textarea__inner) {
          border: none;
          background: transparent;
          font-size: 14px;
          line-height: 1.7;
          padding: 16px;
        }
        
        :deep(.el-input__inner) {
          font-size: 14px;
          font-weight: 500;
          color: #1e293b;
          padding: 0 16px;
        }
        
        // 优化选择器特有的样式
        :deep(.el-input__suffix) {
          right: 12px;
          
          .el-select__caret {
            color: #6366f1;
            font-size: 16px;
            transition: all 0.3s ease;
            
            &.is-reverse {
              transform: rotate(180deg);
            }
          }
        }
        
        // 多选标签优化
        :deep(.el-select__tags) {
          flex-wrap: wrap;
          gap: 4px;
          padding: 6px 12px;
          
          .el-tag {
            background: #eff6ff;
            border: 1px solid #bfdbfe;
            border-radius: 6px;
            color: #1d4ed8;
            font-size: 12px;
            font-weight: 500;
            padding: 2px 6px;
            margin: 0;
            height: auto;
            line-height: 1.4;
            
            .el-tag__close {
              color: #1d4ed8;
              font-size: 12px;
              margin-left: 4px;
              
              &:hover {
                color: #dc2626;
              }
            }
          }
        }
      }


    }
  }
}



// 响应式适配
@media (max-width: 768px) {
  .agent-editor {
    .editor-body {
      .left-panel,
      .center-panel {
        .panel-header {
          padding: 12px 16px;
          flex-wrap: wrap;
          gap: 8px;
        }
        
        .panel-content {
          padding: 16px;
        }
      }
    }
  }
}

// 简化清晰的下拉菜单样式
:global(.el-select-dropdown) {
  background: white !important;
  border: 1px solid #e2e8f0 !important;
  border-radius: 12px !important;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1) !important;
  padding: 6px !important;
  margin-top: 4px !important;
  
  .el-select-dropdown__item {
    border-radius: 8px !important;
    padding: 8px 12px !important;
    margin: 2px 0 !important;
    font-size: 14px !important;
    color: #374151 !important;
    transition: all 0.2s ease !important;
    background: transparent !important;
    
    &:hover {
      background: #f3f4f6 !important;
      color: #111827 !important;
    }
    
    &.selected {
      background: #eff6ff !important;
      color: #2563eb !important;
      font-weight: 600 !important;
    }
  }
  
  .el-select-dropdown__empty {
    color: #6b7280 !important;
    font-size: 14px !important;
    padding: 20px !important;
    text-align: center !important;
  }
}

// 简化的选项样式
.custom-option {
  display: flex !important;
  align-items: center !important;
  gap: 12px !important;
  width: 100% !important;
  padding: 4px 0 !important;
  
  .option-logo {
    width: 20px !important;
    height: 20px !important;
    flex-shrink: 0 !important;
    border-radius: 4px !important;
    object-fit: cover !important;
  }
  
  .option-name {
    flex: 1 !important;
    font-weight: 500 !important;
    color: #111827 !important;
    font-size: 14px !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
  }
  
  .option-badge {
    flex-shrink: 0 !important;
    padding: 2px 6px !important;
    border-radius: 4px !important;
    font-size: 10px !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.5px !important;
    
    &.ai-badge {
      background: #dbeafe !important;
      color: #1d4ed8 !important;
    }
    
    &.kb-badge {
      background: #dcfce7 !important;
      color: #15803d !important;
    }
    
    &.tool-badge {
      background: #fed7aa !important;
      color: #c2410c !important;
    }
    
    &.mcp-badge {
      background: #e9d5ff !important;
      color: #7c2d12 !important;
    }
    
    &.skill-badge {
      background: #fef3c7 !important;
      color: #92400e !important;
    }
  }
}

// 多选标签样式优化
:global(.el-tag.el-tag--info) {
  background: #eff6ff !important;
  border: 1px solid #bfdbfe !important;
  color: #1d4ed8 !important;
  font-weight: 500 !important;
  border-radius: 6px !important;
}
</style> 
