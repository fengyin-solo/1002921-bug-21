<template>
  <section class="page" data-module="gauge">
    <header class="page-head">
      <div>
        <h2>压力表检定管理</h2>
        <p class="page-desc">量程范围、精度等级、下次检定日与检定结论统一由后台判定：越界不允许保存，停用表不进排期，结论以最后一次发布为准。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记压力表</button>
        <button class="btn" type="button" @click="rejudgeHistory">按新规则重判历史数据</button>
        <button class="btn" type="button" @click="exportRows">导出压力表检定清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>压力表编号</span>
        <input v-model="keyword" placeholder="按压力表编号检索" />
      </label>
      <label class="filter-item">
        <span>仪表状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>规则异常</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-stopped': isStopped(row) }">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '压力表编号'">
              <a class="link" href="javascript:void(0)" @click="openDetail(row)">{{ row[column] }}</a>
            </template>
            <template v-else-if="column === '仪表状态'">
              <span class="status-tag" :class="statusClass(row)">{{ row[column] ?? '—' }}</span>
              <span v-if="isStopped(row)" class="stopped-flag">已停用 · 不参与排期</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td>
            <span v-if="violationsOf(row).length" class="violation-flag" :title="violationsOf(row).join('；')">
              {{ violationsOf(row).length }} 项
            </span>
            <span v-else>—</span>
          </td>
          <td class="row-actions">
            <template v-if="!isStopped(row)">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="openAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <a class="link" href="javascript:void(0)" @click="openDetail(row)">查看发布记录</a>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无压力表检定数据，可先登记压力表</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条压力表检定记录，停用表不进入待检定排期</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
    </footer>

    <!-- 登记压力表 -->
    <div v-if="createOpen" class="modal-mask" @click.self="closeCreate">
      <div class="modal">
        <h3>登记压力表</h3>
        <p class="modal-tip">量程范围与精度等级越过允许范围将不允许保存，后台会逐项说明超限原因。</p>
        <form class="modal-form" @submit.prevent="submitCreate">
          <label v-for="field in createFields" :key="field.key" class="form-item">
            <span>{{ field.label }}<em v-if="field.required">*</em></span>
            <select v-if="field.key === '精度等级'" v-model="createForm[field.key]">
              <option value="">请选择精度等级</option>
              <option v-for="grade in gradeOptions" :key="grade" :value="grade">{{ grade }} 级</option>
            </select>
            <input v-else v-model="createForm[field.key]" :placeholder="field.placeholder ?? ''" />
          </label>
          <div class="modal-actions">
            <button class="btn" type="button" @click="closeCreate">取消</button>
            <button class="btn primary" type="submit">保存</button>
          </div>
        </form>
      </div>
    </div>

    <!-- 执行动作 -->
    <div v-if="actionOpen" class="modal-mask" @click.self="closeAction">
      <div class="modal">
        <h3>{{ currentAction }} · {{ currentRow?.['压力表编号'] }}</h3>
        <form class="modal-form" @submit.prevent="submitAction">
          <template v-if="currentAction === '安排检定'">
            <p class="modal-tip">安排检定只登记排期事实；停用表已被排除在排期之外。</p>
          </template>
          <template v-else>
            <label class="form-item">
              <span>检定日期</span>
              <input v-model="actionForm['检定日期']" placeholder="留空取今天，格式 YYYY-MM-DD" />
            </label>
            <label class="form-item">
              <span>检定周期（月）</span>
              <input v-model="actionForm['检定周期']" placeholder="留空沿用当前周期，允许 1~24" />
            </label>
          </template>
          <div class="modal-actions">
            <button class="btn" type="button" @click="closeAction">取消</button>
            <button class="btn primary" type="submit">确认</button>
          </div>
        </form>
      </div>
    </div>

    <!-- 明细与发布记录 -->
    <div v-if="detailOpen" class="modal-mask wide" @click.self="closeDetail">
      <div class="modal">
        <h3>压力表明细 · {{ detailRow?.['压力表编号'] }}</h3>
        <dl class="detail-grid">
          <template v-for="column in columns" :key="column">
            <dt>{{ column }}</dt>
            <dd>{{ detailRow?.[column] ?? '—' }}</dd>
          </template>
        </dl>
        <div v-if="violationsOf(detailRow).length" class="violation-box">
          <strong>规则异常：</strong>
          <ul>
            <li v-for="item in violationsOf(detailRow)" :key="item">{{ item }}</li>
          </ul>
        </div>
        <h4>检定结论发布记录（以最后一次发布为准）</h4>
        <table v-if="(detailRow?.publications ?? []).length" class="data-table inner">
          <thead>
            <tr><th>检定日期</th><th>检定结论</th><th>发布时间</th></tr>
          </thead>
          <tbody>
            <tr
              v-for="(item, index) in [...(detailRow?.publications ?? [])].reverse()"
              :key="index"
              :class="{ 'latest-row': index === 0 }"
            >
              <td>{{ item['检定日期'] }}</td>
              <td>{{ item['检定结论'] }}</td>
              <td>{{ item['发布时间'] }}<span v-if="index === 0" class="stopped-flag">当前生效</span></td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty-state">暂无发布记录，结论按「未检定」处理。</p>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="closeDetail">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | string[] | Publication[] | null>
type Publication = { '检定日期': string; '检定结论': string; '发布时间': string }

function violationsOf(row: Row | null): string[] {
  const value = row?.violations
  return Array.isArray(value) ? (value as string[]) : []
}

const ENDPOINT = '/api/gauge'
const columns = ['压力表编号', '所属设备', '量程范围', '精度等级', '检定日期', '检定周期', '下次检定日', '检定结论', '仪表状态']
const actions = ['安排检定', '登记合格', '登记不合格', '办理停用']
const statuses = ['检定合格', '即将到期', '待检定', '已停用']
const gradeOptions = ['0.25', '0.4', '0.6', '1.0', '1.6', '2.5']
const createFields = [
  { key: '压力表编号', label: '压力表编号', required: true },
  { key: '所属设备', label: '所属设备', required: true },
  { key: '量程范围', label: '量程范围', placeholder: '如 0~1.6 MPa（上限不超过 160 MPa）', required: true },
  { key: '精度等级', label: '精度等级', required: true },
  { key: '检定周期', label: '检定周期（月）', placeholder: '默认 6，允许 1~24' },
  { key: '检定日期', label: '检定日期', placeholder: 'YYYY-MM-DD，首次检定时填写' },
  { key: '检定结论', label: '首次检定结论', placeholder: '合格 / 不合格，可留空' },
]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const stats = ref([
  { label: '检定合格', value: 0 },
  { label: '即将到期', value: 0 },
  { label: '待检定排期（不含停用）', value: 0 },
  { label: '已停用（单独标记）', value: 0 },
])

const createOpen = ref(false)
const createForm = reactive<Record<string, string>>({})

const actionOpen = ref(false)
const currentAction = ref('')
const currentRow = ref<Row | null>(null)
const actionForm = reactive<Record<string, string>>({})

const detailOpen = ref(false)
const detailRow = ref<(Row & { publications?: Publication[] }) | null>(null)

function isStopped(row: Row): boolean {
  return row.status === '已停用' || row['仪表状态'] === '已停用'
}

function statusClass(row: Row): string {
  if (isStopped(row)) return 'tag-stopped'
  if (row.status === '待检定') return 'tag-due'
  if (row.status === '即将到期') return 'tag-expiring'
  return 'tag-pass'
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (keyword.value) params.set('keyword', keyword.value)
  if (statusFilter.value) params.set('status', statusFilter.value)
  try {
    const [listResp, scheduleResp] = await Promise.all([
      request(`${ENDPOINT}?${params.toString()}`),
      request(`${ENDPOINT}/scheduling/pending`),
    ])
    if (!listResp.ok) throw new Error('压力表列表读取失败')
    const payload = await listResp.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (scheduleResp.ok) {
      const schedule = await scheduleResp.json()
      stats.value = [
        { label: '检定合格', value: schedule['检定合格'] ?? 0 },
        { label: '即将到期', value: schedule['即将到期'] ?? 0 },
        { label: '待检定排期（不含停用）', value: schedule['待检定'] ?? 0 },
        { label: '已停用（单独标记）', value: schedule['已停用'] ?? 0 },
      ]
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '压力表检定列表读取失败'
  }
}

// ---------- 登记 ----------

function openCreate() {
  for (const key of Object.keys(createForm)) delete createForm[key]
  createOpen.value = true
  errorMessage.value = ''
}

function closeCreate() {
  createOpen.value = false
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      errorMessage.value = payload.message ?? '压力表登记失败'
      return
    }
    noticeMessage.value = payload.message ?? '压力表已登记'
    closeCreate()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '压力表登记失败'
  }
}

// ---------- 动作 ----------

function openAction(action: string, row: Row) {
  currentAction.value = action
  currentRow.value = row
  actionForm['检定日期'] = ''
  actionForm['检定周期'] = ''
  actionOpen.value = true
  errorMessage.value = ''
}

function closeAction() {
  actionOpen.value = false
  currentRow.value = null
}

async function submitAction() {
  if (!currentRow.value) return
  errorMessage.value = ''
  const values: Record<string, string> = { action: currentAction.value }
  if (actionForm['检定日期']) values['检定日期'] = actionForm['检定日期']
  if (actionForm['检定周期']) values['检定周期'] = actionForm['检定周期']
  try {
    const response = await request(`${ENDPOINT}/${String(currentRow.value.id)}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      errorMessage.value = payload.message ?? '压力表检定动作未生效'
      return
    }
    noticeMessage.value = payload.message ?? '操作已生效'
    closeAction()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '压力表检定操作失败'
  }
}

// ---------- 明细 ----------

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${String(row.id)}`)
    if (!response.ok) throw new Error('压力表明细读取失败')
    detailRow.value = (await response.json()) as Row & { publications?: Publication[] }
    detailOpen.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '压力表明细读取失败'
  }
}

function closeDetail() {
  detailOpen.value = false
  detailRow.value = null
}

// ---------- 规则重判 ----------

async function rejudgeHistory() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/rejudge`, {
      method: 'POST',
      body: JSON.stringify({}),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      errorMessage.value = payload.message ?? '历史数据重判失败'
      return
    }
    noticeMessage.value = payload.message ?? '历史数据已重判'
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '历史数据重判失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.row-stopped {
  color: #999;
  background: #f7f7f8;
}

.status-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  margin-right: 6px;
}
.tag-pass { background: #e6f7ec; color: #1a7f37; }
.tag-expiring { background: #fff4e0; color: #b76e00; }
.tag-due { background: #fde8e8; color: #c02d2d; }
.tag-stopped { background: #ececee; color: #666; }

.stopped-flag {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 12px;
  background: #ececee;
  color: #666;
}

.violation-flag {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  background: #fde8e8;
  color: #c02d2d;
  cursor: help;
}

.notice-text {
  color: #1a7f37;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}

.modal {
  width: 480px;
  max-height: 85vh;
  overflow-y: auto;
  background: #fff;
  border-radius: 10px;
  padding: 20px 24px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18);
}

.modal-mask.wide .modal {
  width: 720px;
}

.modal h3 {
  margin: 0 0 8px;
}

.modal h4 {
  margin: 18px 0 8px;
}

.modal-tip {
  color: #777;
  font-size: 13px;
  margin: 0 0 12px;
}

.modal-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.form-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: #444;
}

.form-item em {
  color: #c02d2d;
  font-style: normal;
  margin-left: 2px;
}

.form-item input,
.form-item select {
  padding: 7px 10px;
  border: 1px solid #d4d4d8;
  border-radius: 6px;
  font-size: 14px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 14px;
}

.detail-grid {
  display: grid;
  grid-template-columns: 130px 1fr;
  gap: 6px 12px;
  margin: 8px 0;
  font-size: 13px;
}

.detail-grid dt {
  color: #888;
}

.detail-grid dd {
  margin: 0;
}

.violation-box {
  border: 1px solid #f0c2c2;
  background: #fdeeee;
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  color: #a02020;
}

.violation-box ul {
  margin: 4px 0 0;
  padding-left: 18px;
}

.data-table.inner {
  margin: 8px 0;
}

.latest-row td {
  font-weight: 600;
}
</style>
