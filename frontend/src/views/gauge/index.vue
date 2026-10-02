<template>
  <section class="page" data-module="gauge">
    <header class="page-head">
      <div>
        <h2>压力表检定管理</h2>
        <p class="page-desc">
          合格性、下次检定日、排期全部由后端统一判定：量程或精度越界不许保存，
          下次检定日按上次检定日期加检定周期计算，停用表不参与排期，两处结论冲突时以最后发布的检定为准。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记压力表</button>
        <button class="btn" type="button" @click="openSchedule">查看待检定排期</button>
        <button class="btn ghost" type="button" @click="runRejudge">按新规则重新判定</button>
        <button class="btn" type="button" @click="exportRows">导出清单</button>
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
        <span>判定状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>判定说明</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-stopped': row.停用 }">
          <td>
            <button class="link" type="button" @click="openDetail(row)">{{ row.压力表编号 }}</button>
            <span v-if="row.停用" class="badge badge-stopped">已停用·不排期</span>
          </td>
          <td>{{ row.所属设备 }}</td>
          <td>{{ row.量程范围 }}</td>
          <td>
            {{ row.精度等级 }} 级
            <span v-if="!row.合规" class="badge badge-bad">越界</span>
          </td>
          <td>{{ row.检定周期 }} 个月</td>
          <td>{{ row.检定日期 }}</td>
          <td>{{ row.下次检定日 }}</td>
          <td><span :class="['badge', badgeClass(row.status)]">{{ row.检定结论 }}</span></td>
          <td>{{ row.仪表状态 }}</td>
          <td class="reason-cell">{{ row.判定说明 }}</td>
          <td class="row-actions">
            <button
              v-if="row.可排期"
              class="link"
              type="button"
              @click="openAction('安排检定', row)"
            >安排检定</button>
            <template v-if="!row.停用 && row.合规">
              <button class="link" type="button" @click="openAction('登记合格', row)">登记合格</button>
              <button class="link danger" type="button" @click="openAction('登记不合格', row)">登记不合格</button>
            </template>
            <button v-if="!row.停用" class="link danger" type="button" @click="runStop(row)">办理停用</button>
            <button class="link" type="button" @click="openDetail(row)">详情</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无符合条件的压力表记录，可先登记压力表</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条压力表记录 · 当前判定规则 v{{ ruleVersion }}</span>
      <span v-if="message" :class="messageError ? 'error-text' : 'ok-text'">{{ message }}</span>
    </footer>

    <!-- 登记 / 编辑弹层 -->
    <div v-if="createOpen || editing" class="modal-mask" @click.self="closeForms">
      <div class="modal">
        <h3>{{ editing ? '修改压力表档案' : '登记压力表' }}</h3>
        <p v-if="editing && editing.停用" class="error-text">该表已停用，档案只读；如需变更请先办理启用。</p>
        <form class="modal-form" @submit.prevent="submitForm">
          <label v-for="field in formFields" :key="field.key" class="form-item">
            <span>{{ field.label }}</span>
            <input
              v-model="form[field.key]"
              :type="field.type || 'text'"
              :placeholder="field.hint"
              :disabled="Boolean(editing && editing.停用)"
            />
            <small v-if="field.hint" class="field-hint">{{ field.hint }}</small>
          </label>
          <p v-if="formErrors.length" class="error-text form-errors">
            <span v-for="(err, i) in formErrors" :key="i">{{ err }}<br /></span>
          </p>
          <div class="modal-actions">
            <button class="btn primary" type="submit" :disabled="Boolean(editing && editing.停用)">保存</button>
            <button class="btn ghost" type="button" @click="closeForms">取消</button>
          </div>
        </form>
      </div>
    </div>

    <!-- 检定动作弹层（补检定日期） -->
    <div v-if="actionContext" class="modal-mask" @click.self="actionContext = null">
      <div class="modal">
        <h3>{{ actionContext.action }} · {{ actionContext.row.压力表编号 }}</h3>
        <form class="modal-form" @submit.prevent="submitAction">
          <label class="form-item">
            <span>{{ actionContext.action === '安排检定' ? '计划检定日' : '本次检定日期' }}</span>
            <input v-model="actionDate" type="date" required />
          </label>
          <div class="modal-actions">
            <button class="btn primary" type="submit">确认</button>
            <button class="btn ghost" type="button" @click="actionContext = null">取消</button>
          </div>
        </form>
      </div>
    </div>

    <!-- 待检定排期弹层 -->
    <div v-if="scheduleOpen" class="modal-mask" @click.self="scheduleOpen = false">
      <div class="modal modal-wide">
        <h3>待检定排期（已停用的表不参与）</h3>
        <table class="data-table">
          <thead>
            <tr><th>压力表编号</th><th>所属设备</th><th>当前状态</th><th>下次检定日</th><th>判定说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in scheduleRows" :key="String(row.id)">
              <td>{{ row.压力表编号 }}</td>
              <td>{{ row.所属设备 }}</td>
              <td><span :class="['badge', badgeClass(row.status)]">{{ row.status }}</span></td>
              <td>{{ row.下次检定日 }}</td>
              <td class="reason-cell">{{ row.判定说明 }}</td>
            </tr>
            <tr v-if="!scheduleRows.length">
              <td colspan="5" class="empty-state">当前没有待检定或即将到期的在役压力表</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="scheduleOpen = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 详情抽屉：与列表页同一份判定结果，附检定发布记录 -->
    <div v-if="detail" class="drawer-mask" @click.self="detail = null">
      <aside class="drawer">
        <header class="drawer-head">
          <h3>{{ detail.压力表编号 }} · 压力表明细</h3>
          <button class="link" type="button" @click="detail = null">关闭</button>
        </header>

        <div class="detail-block">
          <p v-for="item in detailItems" :key="item.label" class="detail-line">
            <span>{{ item.label }}</span><strong>{{ item.value }}</strong>
          </p>
          <p class="detail-line">
            <span>判定状态</span>
            <strong><span :class="['badge', badgeClass(detail.status)]">{{ detail.status }}</span></strong>
          </p>
          <p class="detail-line"><span>判定说明</span><strong>{{ detail.判定说明 }}</strong></p>
        </div>

        <h4>检定发布记录（结论冲突时以最后一条为准）</h4>
        <table class="data-table">
          <thead>
            <tr><th>第几次</th><th>检定日期</th><th>周期(月)</th><th>结论</th><th>适用规则</th></tr>
          </thead>
          <tbody>
            <tr v-for="rec in detail.检定记录" :key="rec.seq" :class="{ 'record-latest': isLatest(rec) }">
              <td>第 {{ rec.seq }} 次<span v-if="isLatest(rec)" class="badge badge-latest">最后发布</span></td>
              <td>{{ rec.检定日期 }}</td>
              <td>{{ rec.检定周期 }}</td>
              <td>{{ rec.检定结论 }}</td>
              <td>v{{ rec.规则版本 }}</td>
            </tr>
            <tr v-if="!detail.检定记录.length">
              <td colspan="5" class="empty-state">尚无检定记录，需安排首检</td>
            </tr>
          </tbody>
        </table>

        <div class="modal-actions">
          <button class="btn" type="button" @click="openEdit(detail)" :disabled="detail.停用">修改档案</button>
        </div>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>
interface VerdictRecord {
  seq: number
  检定日期: string
  检定周期: number
  检定结论: string
  规则版本: number
}

const ENDPOINT = '/api/gauge'
const columns = ['压力表编号', '所属设备', '量程范围', '精度等级', '检定周期', '检定日期', '下次检定日', '检定结论', '仪表状态']
const statuses = ['检定合格', '即将到期', '待检定', '不合规', '已停用']

const formFields: { key: string; label: string; type?: string; hint?: string }[] = [
  { key: '压力表编号', label: '压力表编号' },
  { key: '所属设备', label: '所属设备' },
  { key: '量程范围', label: '量程范围', hint: '形如 0~1.6 MPa，允许 0~100 MPa，越界不许保存' },
  { key: '精度等级', label: '精度等级', hint: '允许 0.25、0.4、0.6、1.0、1.6、2.5、4.0 级' },
  { key: '检定周期', label: '检定周期（月，默认 6）', type: 'number', hint: '下次检定日 = 上次检定日期 + 检定周期' },
] as const

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref<{ label: string; value: number }[]>([
  { label: '合格仪表', value: 0 },
  { label: '待检定仪表', value: 0 },
  { label: '即将到期', value: 0 },
  { label: '停用仪表', value: 0 },
  { label: '不合规', value: 0 },
])
const ruleVersion = ref(1)
const message = ref('')
const messageError = ref(false)

const keyword = ref('')
const statusFilter = ref('')

const createOpen = ref(false)
const editing = ref<Row | null>(null)
const form = reactive<Record<string, string>>({})
const formErrors = ref<string[]>([])

const actionContext = ref<{ action: string; row: Row } | null>(null)
const actionDate = ref(todayISO())

const scheduleOpen = ref(false)
const scheduleRows = ref<Row[]>([])

const detail = ref<Row | null>(null)

function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

function notify(text: string, isError = false): void {
  message.value = text
  messageError.value = isError
}

function badgeClass(status: string): string {
  if (status === '检定合格') return 'badge-ok'
  if (status === '即将到期') return 'badge-warn'
  if (status === '已停用') return 'badge-stopped'
  if (status === '不合规') return 'badge-bad'
  return 'badge-due'
}

function isLatest(rec: VerdictRecord): boolean {
  const records: VerdictRecord[] = detail.value?.检定记录 ?? []
  return Boolean(records.length) && rec.seq === records[records.length - 1].seq
}

const detailItems = computed(() => {
  if (!detail.value) return []
  return [
    { label: '所属设备', value: detail.value.所属设备 },
    { label: '量程范围', value: `${detail.value.量程范围} MPa` },
    { label: '精度等级', value: `${detail.value.精度等级} 级` },
    { label: '检定周期', value: `${detail.value.检定周期} 个月` },
    { label: '上次检定日期', value: detail.value.检定日期 },
    { label: '下次检定日（日期+周期统一算出）', value: detail.value.下次检定日 },
    { label: '检定结论（以最后发布为准）', value: detail.value.检定结论 },
    { label: '仪表状态', value: detail.value.仪表状态 },
    { label: '是否停用', value: detail.value.停用 ? '是（不参与排期）' : '否' },
    { label: '规格是否合规', value: detail.value.合规 ? '合规' : '不合规' },
    { label: '判定规则版本', value: `v${detail.value.规则版本}` },
  ]
})

function resetFilters(): void {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows(): void {
  window.open(`${ENDPOINT}/export`, '_blank')
}

// ---- 登记 / 编辑 ----

function resetForm(source?: Row): void {
  for (const field of formFields) {
    form[field.key] = source ? String(source[field.key] ?? '') : ''
  }
  formErrors.value = []
}

function openCreate(): void {
  editing.value = null
  resetForm()
  createOpen.value = true
}

function openEdit(row: Row): void {
  detail.value = null
  editing.value = row
  resetForm(row)
}

function closeForms(): void {
  createOpen.value = false
  editing.value = null
  formErrors.value = []
}

async function submitForm(): Promise<void> {
  formErrors.value = []
  const values: Record<string, string> = { ...form }
  if (!values.检定周期) delete values.检定周期
  const id = editing.value?.id
  const response = await request(id ? `${ENDPOINT}/${id}` : ENDPOINT, {
    method: id ? 'PUT' : 'POST',
    body: JSON.stringify({ values }),
  })
  const payload = await response.json()
  if (!response.ok || !payload.ok) {
    // 后端逐项说明越了哪一项（量程 / 精度 / 周期），原样展示。
    formErrors.value = [payload.detail || payload.message || '保存失败']
    return
  }
  notify(payload.message)
  closeForms()
  await reload()
}

// ---- 动作流转 ----

function openAction(action: string, row: Row): void {
  actionContext.value = { action, row }
  actionDate.value = todayISO()
}

async function submitAction(): Promise<void> {
  if (!actionContext.value) return
  const { action, row } = actionContext.value
  const response = await request(`${ENDPOINT}/${row.id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action, 检定日期: actionDate.value }),
  })
  const payload = await response.json()
  actionContext.value = null
  if (!response.ok || !payload.ok) {
    notify(payload.detail || payload.message || '动作未生效', true)
    return
  }
  notify(payload.message)
  await reload()
}

async function runStop(row: Row): Promise<void> {
  const response = await request(`${ENDPOINT}/${row.id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action: '办理停用' }),
  })
  const payload = await response.json()
  notify(payload.message, !payload.ok)
  await reload()
}

// ---- 排期 / 重判 / 详情 ----

async function openSchedule(): Promise<void> {
  scheduleRows.value = []
  const response = await request(`${ENDPOINT}/schedule`)
  const payload = await response.json()
  scheduleRows.value = payload.items ?? []
  scheduleOpen.value = true
}

async function runRejudge(): Promise<void> {
  const response = await request(`${ENDPOINT}/rejudge`, { method: 'POST' })
  const payload = await response.json()
  notify(payload.message || '已按新规则重新判定', !payload.ok)
  await reload()
}

async function openDetail(row: Row): Promise<void> {
  const response = await request(`${ENDPOINT}/${row.id}`)
  detail.value = response.ok ? await response.json() : row
}

// ---- 列表加载 ----

async function reload(): Promise<void> {
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const [listResponse, statsResponse] = await Promise.all([
      request(`${ENDPOINT}?${query.toString()}`),
      request(`${ENDPOINT}/stats`),
    ])
    if (!listResponse.ok) throw new Error('压力表列表读取失败')
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    const first = rows.value[0]
    if (first) ruleVersion.value = Number(first.规则版本 ?? 1)
    if (statsResponse.ok) {
      const statsPayload = await statsResponse.json()
      if (Array.isArray(statsPayload.stats)) stats.value = statsPayload.stats
    }
  } catch (error) {
    notify(error instanceof Error ? error.message : '压力表检定列表读取失败', true)
  }
}

onMounted(reload)
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.badge { display: inline-block; border-radius: 10px; padding: 1px 8px; font-size: 12px; margin-left: 4px; }
.badge-ok { background: #e7f6ec; color: #1a7f37; }
.badge-warn { background: #fdf3dc; color: #9a6700; }
.badge-due { background: #fde8e8; color: #b42318; }
.badge-bad { background: #fde8e8; color: #b42318; font-weight: 600; }
.badge-stopped { background: #eef0f3; color: #667085; }
.badge-latest { background: #1f6feb; color: #fff; }
.row-stopped { background: #f7f8fa; color: #667085; }
.row-stopped button.link:not(.danger) { color: #98a2b3; }
.link.danger { color: #b42318; }
.reason-cell { color: var(--muted); font-size: 12px; max-width: 260px; }
.ok-text { color: #1a7f37; }

.modal-mask, .drawer-mask {
  position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45);
  display: flex; align-items: center; justify-content: center; z-index: 50;
}
.modal { background: #fff; border-radius: 10px; padding: 20px 24px; width: 460px; max-height: 86vh; overflow: auto; }
.modal-wide { width: 760px; }
.modal h3, .drawer h3 { margin: 0 0 12px; }
.modal-form { display: flex; flex-direction: column; gap: 10px; }
.form-item { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.form-item input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.field-hint { color: var(--muted); }
.form-errors { margin: 0; font-size: 12px; }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }

.drawer-mask { justify-content: flex-end; }
.drawer { background: #fff; width: 560px; max-width: 92vw; height: 100%; padding: 20px 24px; overflow: auto; }
.drawer-head { display: flex; justify-content: space-between; align-items: center; }
.detail-block { border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; margin-bottom: 14px; }
.detail-line { display: flex; justify-content: space-between; gap: 12px; margin: 6px 0; font-size: 13px; }
.detail-line span { color: var(--muted); }
.detail-line strong { text-align: right; font-weight: 600; }
.drawer h4 { margin: 12px 0 8px; font-size: 14px; }
.record-latest { background: #f0f6ff; }
</style>
