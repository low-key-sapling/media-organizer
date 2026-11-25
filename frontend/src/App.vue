<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import type { Component } from "vue";
import {
  Layers3,
  LayoutDashboard,
  History,
  SlidersHorizontal,
  FolderOpen,
  Folder,
  ArrowRight,
  ArrowUp,
  ArrowLeft,
  Plus,
  Search,
  ChevronLeft,
  ChevronRight,
  Check,
  CheckCheck,
  ShieldCheck,
  Shield,
  ScanLine,
  Camera,
  Image,
  Film,
  Music2,
  FileText,
  Archive,
  File,
  X,
  Pencil,
  Download,
  Play,
  Pause,
  RotateCcw,
  CircleAlert,
  CircleCheck,
  HardDrive,
  MapPin,
  CalendarDays,
  WifiOff,
  LoaderCircle,
  ExternalLink,
  CheckCircle2,
} from "lucide-vue-next";

interface Job {
  id: string;
  source: string;
  target: string;
  status: string;
  phase: string;
  verification: string;
  file_count: number;
  total_bytes: number;
  processed_count: number;
  copied_bytes: number;
  verified_count: number;
  checked_count?: number;
  active_file_bytes?: number;
  active_verify_bytes?: number;
  checked_bytes?: number;
  safe_to_delete: boolean;
  created_at: string;
  validated_at?: string;
  error?: string;
  warnings?: string[];
  changes?: string[];
  changes_count?: number;
  issue_count?: number;
  unhandled_count?: number;
  report_path?: string;
  current_file?: string;
  stats?: {
    categories: Record<string, number>;
    statuses: Record<string, number>;
  };
}
interface Entry {
  id: number;
  source_rel: string;
  target_rel: string;
  category: string;
  month: string;
  location: string;
  size: number;
  status: string;
  kind: string;
  warnings: string[];
  error: string;
  date_source?: string;
  location_source?: string;
  type_source?: string;
  manual?: boolean;
  content_verified?: boolean;
}
interface Settings {
  source: string;
  target: string;
  verification: string;
  max_city_distance_km: number;
  extension_mapping: Record<string, string>;
}
interface Directory {
  name: string;
  path: string;
  link: boolean;
}

const labels: Record<string, string> = {
  photos: "照片",
  images: "图片",
  videos: "视频",
  audio: "音频",
  documents: "文档",
  archives: "压缩包",
  others: "其他",
};
const icons: Record<string, Component> = {
  photos: Camera,
  images: Image,
  videos: Film,
  audio: Music2,
  documents: FileText,
  archives: Archive,
  others: File,
};
const statuses: Record<string, string> = {
  queued_scan: "等待扫描",
  scanning: "扫描中",
  ready: "待执行",
  queued: "等待执行",
  copying: "复制中",
  verifying: "校验中",
  paused: "已暂停",
  completed: "已完成",
  completed_issues: "需处理",
  failed: "失败",
  scan_interrupted: "扫描中断",
  planned: "待复制",
  copied: "已复制",
  reused: "已复用",
  verified: "已校验",
  unhandled: "未处理",
};
const levels = [
  {
    id: "quick",
    name: "快速校验",
    desc: "核对路径、文件数量和大小",
    icon: ScanLine,
  },
  {
    id: "sampled",
    name: "抽样校验",
    desc: "额外抽取 10% 文件校验内容",
    icon: Shield,
  },
  {
    id: "full",
    name: "完整校验",
    desc: "逐一核对全部文件的内容",
    icon: ShieldCheck,
  },
];
const page = ref("workspace"),
  jobs = ref<Job[]>([]),
  current = ref<Job | null>(null),
  files = ref<Entry[]>([]),
  total = ref(0),
  filePage = ref(1),
  filter = ref(""),
  search = ref(""),
  problemOnly = ref(false);
const meta = ref<any>(null),
  settings = ref<Settings>({
    source: "",
    target: "",
    verification: "full",
    max_city_distance_km: 100,
    extension_mapping: {},
  });
const source = ref(""),
  target = ref(""),
  verification = ref("full"),
  selected = ref(new Set<number>()),
  loading = ref(false),
  connected = ref(true),
  toast = ref(""),
  toastError = ref(false);
const showConfirm = ref(false),
  showEdit = ref(false),
  editCategory = ref(""),
  editMonth = ref(""),
  editLocation = ref(""),
  rules = ref<{ extension: string; category: string }[]>([]);
const browser = ref<{
    kind: "source" | "target";
    path: string;
    parent: string;
    directories: Directory[];
  } | null>(null),
  browserPath = ref(""),
  browserBusy = ref(false);
const busyStates = [
  "queued_scan",
  "scanning",
  "queued",
  "copying",
  "verifying",
];
const busy = computed(
  () => !!current.value && busyStates.includes(current.value.status),
);
const editable = computed(() => current.value?.status === "ready");
const done = computed(() =>
  ["completed", "completed_issues"].includes(current.value?.status || ""),
);
const processed = computed(() =>
  current.value?.status === "verifying"
    ? current.value.checked_count || 0
    : current.value?.processed_count || 0,
);
const progress = computed(() => {
  if (!current.value?.file_count) return 0;
  const job = current.value as Job & {
    active_file_bytes?: number;
    active_verify_bytes?: number;
    checked_bytes?: number;
  };
  const amount =
    job.status === "verifying"
      ? (job.checked_bytes || 0) + (job.active_verify_bytes || 0)
      : job.copied_bytes + (job.active_file_bytes || 0);
  return Math.min(
    100,
    Math.round(
      (job.total_bytes
        ? amount / job.total_bytes
        : processed.value / job.file_count) * 100,
    ),
  );
});
const progressCapacity = computed(() => {
  const job = current.value;
  if (!job) return "";
  const amount =
    job.status === "verifying"
      ? (job.checked_bytes || 0) + (job.active_verify_bytes || 0)
      : job.copied_bytes + (job.active_file_bytes || 0);
  return `${job.status === "verifying" ? "已核对" : "已处理"} ${size(amount)} / ${size(job.total_bytes)}`;
});
const latest = computed(() => jobs.value.slice(0, 4)),
  totalJobs = computed(
    () => jobs.value.filter((j) => j.status === "completed").length,
  );
const selectedAll = computed(
  () =>
    files.value.filter((e) => e.kind === "file").length > 0 &&
    files.value
      .filter((e) => e.kind === "file")
      .every((e) => selected.value.has(e.id)),
);
const allPages = computed(() => Math.max(1, Math.ceil(total.value / 50)));
let poll: ReturnType<typeof setInterval>,
  toastTimer: ReturnType<typeof setTimeout>,
  searchTimer: ReturnType<typeof setTimeout>,
  polling = false;

async function api<T = any>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api" + url, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string" ? body.detail : "操作未完成，请检查输入",
    );
  }
  return response.json();
}
function notice(message: string, error = false) {
  toast.value = message;
  toastError.value = error;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 5000);
}
function size(value: number = 0) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const power = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** power).toFixed(power > 1 ? 1 : 0)} ${units[power]}`;
}
function date(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function name(path: string) {
  return path.split(/[\\/]/).pop() || path;
}
function place(value: string) {
  return value === "others" ? "其他地点" : value.replaceAll("_", " / ");
}
function basis(entry: Entry) {
  return [
    entry.type_source,
    entry.date_source?.startsWith("metadata")
      ? "媒体记录时间"
      : entry.date_source === "filename"
        ? "文件名日期"
        : "文件修改时间",
    entry.location_source === "gps_approximate"
      ? "GPS 推算地点"
      : entry.location_source === "metadata"
        ? "文件记录地点"
        : "未确定地点",
    ...(entry.warnings || []),
  ]
    .filter(Boolean)
    .join(" · ");
}
function setRules() {
  rules.value = Object.entries(settings.value.extension_mapping).map(
    ([extension, category]) => ({ extension, category }),
  );
}
async function refresh() {
  if (polling) return;
  polling = true;
  try {
    jobs.value = await api("/jobs");
    connected.value = true;
    if (current.value) {
      const before = current.value.status;
      current.value = await api("/jobs/" + current.value.id);
      if (
        page.value === "workspace" &&
        (before !== current.value!.status || busy.value)
      )
        await loadFiles();
    }
  } catch {
    connected.value = false;
  } finally {
    polling = false;
  }
}
async function loadFiles() {
  if (!current.value) return;
  const query = new URLSearchParams({
    page: String(filePage.value),
    search: search.value,
    category: filter.value,
    status: problemOnly.value ? "issues" : "",
  });
  const result = await api(`/jobs/${current.value.id}/files?${query}`);
  files.value = result.items;
  total.value = result.total;
}
async function selectJob(job: Job) {
  current.value = await api("/jobs/" + job.id);
  page.value = "workspace";
  verification.value = job.verification;
  search.value = "";
  filter.value = "";
  problemOnly.value = false;
  filePage.value = 1;
  selected.value.clear();
  await loadFiles();
}
function newTask() {
  if (busy.value) return notice("请先暂停或等待当前任务完成", true);
  current.value = null;
  page.value = "workspace";
  files.value = [];
  selected.value.clear();
}
async function scan() {
  if (!source.value.trim() || !target.value.trim())
    return notice("请选择源目录和目标目录", true);
  loading.value = true;
  try {
    current.value = await api("/jobs", {
      method: "POST",
      body: JSON.stringify({
        source: source.value.trim(),
        target: target.value.trim(),
        verification: verification.value,
      }),
    });
    settings.value = await api("/settings", {
      method: "PUT",
      body: JSON.stringify({
        ...settings.value,
        source: source.value.trim(),
        target: target.value.trim(),
        verification: verification.value,
      }),
    });
    files.value = [];
    filePage.value = 1;
    filter.value = "";
    search.value = "";
    problemOnly.value = false;
    await refresh();
  } catch (error: any) {
    notice(error.message, true);
  } finally {
    loading.value = false;
  }
}
async function execute(verifyOnly = false) {
  if (!current.value) return;
  showConfirm.value = false;
  loading.value = true;
  try {
    current.value = await api(`/jobs/${current.value.id}/execute`, {
      method: "POST",
      body: JSON.stringify({
        verification: verifyOnly ? "full" : verification.value,
        verify_only: verifyOnly,
      }),
    });
    await refresh();
  } catch (error: any) {
    notice(error.message, true);
  } finally {
    loading.value = false;
  }
}
async function pause() {
  if (!current.value) return;
  try {
    await api(`/jobs/${current.value.id}/pause`, { method: "POST" });
    notice("正在保存进度，将在当前操作结束后暂停");
  } catch (error: any) {
    notice(error.message, true);
  }
}
function toggleAll() {
  if (selectedAll.value) selected.value.clear();
  else
    files.value
      .filter((e) => e.kind === "file")
      .forEach((e) => selected.value.add(e.id));
}
function edit(entry?: Entry) {
  if (entry) selected.value = new Set([entry.id]);
  editCategory.value = "";
  editMonth.value = "";
  editLocation.value = "";
  showEdit.value = true;
}
async function saveEdit() {
  if (!current.value) return;
  try {
    await api(`/jobs/${current.value.id}/files`, {
      method: "PATCH",
      body: JSON.stringify({
        ids: [...selected.value],
        category: editCategory.value || null,
        month: editMonth.value || null,
        location: editLocation.value || null,
      }),
    });
    showEdit.value = false;
    selected.value.clear();
    await loadFiles();
    current.value = await api("/jobs/" + current.value.id);
    notice("整理预览已更新");
  } catch (error: any) {
    notice(error.message, true);
  }
}
async function browse(kind: "source" | "target", path?: string) {
  browserBusy.value = true;
  if (!browser.value)
    browser.value = { kind, path: "", parent: "", directories: [] };
  try {
    const result = await api(
      "/directories?" +
        new URLSearchParams({
          path: path ?? (kind === "source" ? source.value : target.value),
        }),
    );
    browser.value = { kind, ...result };
    browserPath.value = result.path;
  } catch (error: any) {
    notice(error.message, true);
    if (browser.value && !browser.value.path) {
      const result = await api("/directories");
      browser.value = { kind, ...result };
      browserPath.value = "";
    }
  } finally {
    browserBusy.value = false;
  }
}
function chooseDirectory() {
  if (!browser.value?.path) return;
  if (browser.value.kind === "source") source.value = browser.value.path;
  else target.value = browser.value.path;
  browser.value = null;
}
async function openFolder(kind: string) {
  if (!current.value) return;
  try {
    await api(`/jobs/${current.value.id}/open`, {
      method: "POST",
      body: JSON.stringify({ kind }),
    });
  } catch (error: any) {
    notice(error.message, true);
  }
}
async function saveSettings() {
  const extension_mapping: Record<string, string> = {};
  for (const rule of rules.value) {
    if (!/^[a-zA-Z0-9]+$/.test(rule.extension))
      return notice("扩展名请填写字母或数字，不需要点号", true);
    extension_mapping[rule.extension.toLowerCase()] = rule.category;
  }
  try {
    settings.value = await api("/settings", {
      method: "PUT",
      body: JSON.stringify({ ...settings.value, extension_mapping }),
    });
    source.value = settings.value.source;
    target.value = settings.value.target;
    verification.value = settings.value.verification;
    notice("配置已保存，网页与命令行均可复用");
  } catch (error: any) {
    notice(error.message, true);
  }
}
function exportConfig() {
  const blob = new Blob([JSON.stringify(settings.value, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "media-organizer-config.json";
  anchor.click();
  URL.revokeObjectURL(url);
}
async function importConfig(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  try {
    settings.value = await api("/settings", {
      method: "PUT",
      body: JSON.stringify(JSON.parse(await file.text())),
    });
    setRules();
    source.value = settings.value.source;
    target.value = settings.value.target;
    verification.value = settings.value.verification;
    notice("配置已导入");
  } catch (error: any) {
    notice(error.message, true);
  } finally {
    input.value = "";
  }
}
function pageFiles(delta: number) {
  filePage.value += delta;
  selected.value.clear();
  loadFiles().catch((error) => notice(error.message, true));
}
watch([filter, problemOnly], () => {
  filePage.value = 1;
  selected.value.clear();
  loadFiles().catch((error) => notice(error.message, true));
});
watch(search, () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    filePage.value = 1;
    selected.value.clear();
    loadFiles().catch((error) => notice(error.message, true));
  }, 300);
});
onMounted(async () => {
  try {
    [meta.value, settings.value, jobs.value] = await Promise.all([
      api("/meta"),
      api("/settings"),
      api("/jobs"),
    ]);
    source.value = settings.value.source;
    target.value = settings.value.target;
    verification.value = settings.value.verification;
    setRules();
    const active = jobs.value.find(
      (j) => busyStates.includes(j.status) || j.status === "paused",
    );
    if (active) await selectJob(active);
  } catch (error: any) {
    connected.value = false;
    notice(error.message, true);
  }
  poll = setInterval(refresh, 1600);
});
onUnmounted(() => {
  clearInterval(poll);
  clearTimeout(toastTimer);
  clearTimeout(searchTimer);
});
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="#" @click.prevent="page = 'workspace'"
        ><span class="brand-icon"><Layers3 :size="24" /></span
        ><span>媒体整理器<small>MEDIA ORGANIZER</small></span></a
      >
      <div class="nav-label">工作空间</div>
      <nav aria-label="主导航">
        <button
          :class="{ active: page === 'workspace' }"
          @click="page = 'workspace'"
        >
          <LayoutDashboard :size="19" />整理工作台
        </button>
        <button
          :class="{ active: page === 'history' }"
          @click="page = 'history'"
        >
          <History :size="19" />任务历史<span
            v-if="jobs.length"
            class="nav-count"
            >{{ jobs.length }}</span
          >
        </button>
        <button
          :class="{ active: page === 'settings' }"
          @click="
            page = 'settings';
            setRules();
          "
        >
          <SlidersHorizontal :size="19" />规则与设置
        </button>
      </nav>
      <div class="sidebar-note">
        <span class="note-icon"><ShieldCheck :size="22" /></span
        ><strong>文件留在你的设备上</strong>
        <p>本地处理，离线可用。<br />每一次整理，都有迹可循。</p>
      </div>
      <div class="sidebar-footer">
        <span class="status-dot" :class="{ disconnected: !connected }"></span
        >{{ connected ? "本地服务已连接" : "正在重新连接"
        }}<small>v{{ meta?.version || "0.1.0" }}</small>
      </div>
    </aside>

    <div class="main-shell">
      <header class="topbar">
        <div>
          <span class="breadcrumb">工作空间</span
          ><ChevronRight :size="14" /><strong>{{
            page === "workspace"
              ? "整理工作台"
              : page === "history"
                ? "任务历史"
                : "规则与设置"
          }}</strong>
        </div>
        <span class="offline-badge"
          ><WifiOff :size="13" />离线模式<span class="tiny-dot"></span
          >本机运行</span
        >
      </header>
      <main>
        <div v-if="!connected" class="connection-banner">
          <CircleAlert
            :size="17"
          />本地服务连接中断。任务由后台程序执行，重新连接后会更新进度。<button
            @click="refresh"
          >
            重新连接
          </button>
        </div>

        <template v-if="page === 'workspace' && !current">
          <div class="page-heading">
            <div>
              <div class="eyebrow">YOUR FILES, IN ORDER</div>
              <h1>让散落的文件，各归其位<span>。</span></h1>
              <p>按类型、月份和地点整理文件，完整复制，逐一核验。</p>
            </div>
            <div class="heading-decoration">
              <FolderOpen :size="36" /><CheckCheck :size="18" />
            </div>
          </div>
          <div class="overview-strip">
            <div>
              <span class="mini-icon green"><FolderOpen :size="18" /></span>
              <div>
                <strong>7 种基础分类</strong
                ><small>照片、图片、视频及更多</small>
              </div>
            </div>
            <div>
              <span class="mini-icon blue"><ShieldCheck :size="18" /></span>
              <div>
                <strong>复制后校验</strong><small>源文件始终由你掌握</small>
              </div>
            </div>
            <div>
              <span class="mini-icon amber"><History :size="18" /></span>
              <div>
                <strong>{{ totalJobs }} 个已完成任务</strong
                ><small>清单与结果长期可追溯</small>
              </div>
            </div>
          </div>
          <div class="workspace-grid">
            <section class="panel create-panel">
              <div class="panel-title">
                <span class="step-number">01</span>
                <div>
                  <h2>新建整理任务</h2>
                  <p>从一个目录开始，建立有序的媒体库</p>
                </div>
              </div>
              <form @submit.prevent="scan">
                <label class="field-label" for="source"
                  >源目录 <span>待整理的文件</span></label
                >
                <div class="path-field">
                  <Folder :size="19" /><input
                    id="source"
                    v-model="source"
                    placeholder="选择或输入待整理目录的完整路径"
                    autocomplete="off"
                  /><button type="button" @click="browse('source')">
                    浏览
                  </button>
                </div>
                <div class="path-connector">
                  <span></span><ArrowRight :size="14" />复制并归档，保留源文件
                </div>
                <label class="field-label" for="target"
                  >目标目录 <span>整理后的文件保存位置</span></label
                >
                <div class="path-field">
                  <FolderOpen :size="19" /><input
                    id="target"
                    v-model="target"
                    placeholder="选择独立的目标目录，也可以输入新目录"
                    autocomplete="off"
                  /><button type="button" @click="browse('target')">
                    浏览
                  </button>
                </div>
                <div class="field-hint">
                  源目录与目标目录需相互独立，支持递归整理全部子目录。
                </div>
                <div class="section-divider"></div>
                <label class="field-label"
                  >校验强度 <span>根据速度和完整性选择</span></label
                >
                <div class="verification-options">
                  <button
                    v-for="level in levels"
                    :key="level.id"
                    type="button"
                    :class="[
                      'verification-option',
                      { chosen: verification === level.id },
                    ]"
                    @click="verification = level.id"
                  >
                    <span class="radio-dot"></span
                    ><component :is="level.icon" :size="21" /><strong
                      >{{ level.name
                      }}<em v-if="level.id === 'full'">推荐</em></strong
                    ><small>{{ level.desc }}</small>
                  </button>
                </div>
                <div class="verification-hint">
                  <ShieldCheck
                    :size="15"
                  />只有完整校验全部通过，才会提供删除源目录的建议。
                </div>
                <div class="form-footer">
                  <span><ScanLine :size="15" />先预览分类结果，再开始复制</span
                  ><button
                    class="button primary"
                    type="submit"
                    :disabled="loading"
                  >
                    <LoaderCircle
                      v-if="loading"
                      class="spin"
                      :size="17"
                    /><ScanLine v-else :size="17" />扫描并预览<ArrowRight
                      :size="16"
                    />
                  </button>
                </div>
              </form>
            </section>
            <aside class="guide-column">
              <section class="panel guide-panel">
                <span class="small-overline">A LITTLE MORE ORDER</span>
                <h2>整理之后，<br />一目了然。</h2>
                <p>每个文件，都有清晰的去处。</p>
                <div class="directory-tree">
                  <div><FolderOpen :size="17" />organized<span>/</span></div>
                  <div class="tree-child">
                    <Camera :size="16" /><b>photos</b><span>/</span>
                  </div>
                  <div class="tree-child indent">
                    <CalendarDays :size="14" />202611<span>/</span>
                  </div>
                  <div class="tree-child indent-deep">
                    <MapPin :size="14" />CN_Zhejiang_Hangzhou
                  </div>
                  <div class="tree-child">
                    <Image :size="16" /><b>images</b><span>/</span>
                  </div>
                  <div class="tree-child">
                    <Film :size="16" /><b>videos</b><span>/</span>
                  </div>
                  <div class="tree-child muted">
                    <Music2 :size="16" />audio<span>/</span><i>…</i>
                  </div>
                </div>
                <div class="guide-bottom">
                  <CheckCircle2 :size="16" />地点无法确定时，归入 others
                </div>
              </section>
              <section class="resource-card">
                <div>
                  <span
                    class="status-dot"
                    :class="{ disconnected: !meta?.metadata_available }"
                  ></span
                  ><strong>媒体信息识别</strong
                  ><span>{{
                    meta?.metadata_available ? "已就绪" : "组件待准备"
                  }}</span>
                </div>
                <div>
                  <span
                    class="status-dot"
                    :class="{ disconnected: !meta?.places?.available }"
                  ></span
                  ><strong>离线城市库</strong
                  ><span>{{
                    meta?.places?.available ? "已就绪" : "数据待准备"
                  }}</span>
                </div>
              </section>
            </aside>
          </div>
          <section class="recent-section">
            <div class="section-heading">
              <h2>最近任务</h2>
              <button class="text-button" @click="page = 'history'">
                查看全部<ArrowRight :size="14" />
              </button>
            </div>
            <div v-if="!latest.length" class="empty-recent">
              <History :size="21" /><span
                >还没有整理记录。完成第一次任务后，结果会保存在这里。</span
              >
            </div>
            <div v-else class="recent-list">
              <button
                v-for="job in latest"
                :key="job.id"
                @click="selectJob(job)"
              >
                <span class="recent-folder"><FolderOpen :size="19" /></span
                ><span
                  ><strong>{{ name(job.source) }}</strong
                  ><small
                    >{{ job.file_count.toLocaleString() }} 个文件 ·
                    {{ size(job.total_bytes) }}</small
                  ></span
                ><span :class="['status-label', job.status]">{{
                  statuses[job.status]
                }}</span
                ><time>{{ date(job.created_at) }}</time
                ><ChevronRight :size="17" />
              </button>
            </div>
          </section>
        </template>

        <template v-else-if="page === 'workspace' && current">
          <div class="page-heading compact">
            <div>
              <button class="back-button" @click="newTask">
                <ArrowLeft :size="15" />整理工作台
              </button>
              <h1>
                {{
                  busy
                    ? current.phase
                    : editable
                      ? "检查整理预览"
                      : done
                        ? "任务结果"
                        : "任务详情"
                }}
              </h1>
              <p>
                {{ current.file_count.toLocaleString() }} 个文件 ·
                {{ size(current.total_bytes)
                }}<span class="job-id">#{{ current.id }}</span>
              </p>
            </div>
            <div class="heading-actions">
              <button v-if="!busy" class="button secondary" @click="newTask">
                <Plus :size="16" />新建任务</button
              ><button
                v-if="current.report_path"
                class="button secondary"
                @click="openFolder('report')"
              >
                <FolderOpen :size="16" />报告目录</button
              ><a
                v-if="current.report_path"
                class="button secondary"
                :href="`/api/jobs/${current.id}/report.csv`"
              >
                <Download :size="16" />导出清单
              </a>
            </div>
          </div>
          <div class="task-paths">
            <div>
              <span>源目录</span>
              <p :title="current.source">{{ current.source }}</p>
            </div>
            <ArrowRight :size="18" />
            <div>
              <span>目标目录</span>
              <p :title="current.target">{{ current.target }}</p>
            </div>
          </div>
          <div v-if="current.safe_to_delete" class="result-banner success">
            <span class="result-icon"><ShieldCheck :size="27" /></span>
            <div>
              <h3>全部文件已通过完整校验</h3>
              <p>
                确认分类结果后，可以手动删除源目录。此结论基于
                {{ date(current.validated_at!) }}
                的校验，请避免继续向源目录写入。
              </p>
            </div>
            <button class="button secondary" @click="openFolder('target')">
              查看整理结果<ExternalLink :size="15" />
            </button>
          </div>
          <div
            v-else-if="done && current.status === 'completed'"
            class="result-banner amber-banner"
          >
            <span class="result-icon"><Shield :size="27" /></span>
            <div>
              <h3>复制完成，尚未完成全部内容校验</h3>
              <p>
                {{
                  current.verification === "sampled"
                    ? `已抽样校验 ${current.verified_count} 个文件。`
                    : "已完成文件路径与大小核对。"
                }}执行完整校验后，才能获得删除源目录的建议。
              </p>
            </div>
            <button class="button secondary" @click="execute(true)">
              执行完整校验
            </button>
          </div>
          <div
            v-else-if="
              current.status === 'completed_issues' ||
              current.status === 'failed' ||
              current.status === 'scan_interrupted'
            "
            class="result-banner error-banner"
          >
            <span class="result-icon"><CircleAlert :size="27" /></span>
            <div>
              <h3>还有项目需要处理</h3>
              <p>
                {{
                  current.error ||
                  `${current.issue_count || 0} 个失败项 · ${current.unhandled_count || 0} 个未处理项 · ${current.changes_count || 0} 处源目录变化`
                }}。请查看清单与报告，暂勿删除源目录。
              </p>
              <small v-if="current.changes?.length">{{
                current.changes.slice(0, 3).join("；")
              }}</small>
            </div>
          </div>
          <div v-if="current.warnings?.length" class="inline-warning">
            <CircleAlert :size="16" /><span>{{
              current.warnings.join("；")
            }}</span>
          </div>
          <section
            v-if="busy || current.status === 'paused'"
            class="panel progress-panel"
          >
            <div class="progress-header">
              <div>
                <LoaderCircle v-if="busy" class="spin" :size="19" /><Pause
                  v-else
                  :size="19"
                /><strong>{{ current.phase }}</strong>
              </div>
              <button v-if="busy" class="button secondary small" @click="pause">
                <Pause :size="14" />暂停任务</button
              ><button v-else class="button primary small" @click="execute()">
                <Play :size="14" />继续任务
              </button>
            </div>
            <div class="progress-track">
              <span
                :style="{
                  width: current.status === 'scanning' ? '32%' : progress + '%',
                }"
                :class="{ indeterminate: current.status === 'scanning' }"
              ></span>
            </div>
            <div class="progress-footer">
              <span :title="current.current_file">{{
                current.current_file || "正在准备文件清单…"
              }}</span><strong>{{
                current.status === "scanning"
                  ? `${current.file_count.toLocaleString()} 个文件`
                  : progress + "%"
              }}</strong>
            </div>
            <p
              v-if="
                current.status !== 'scanning' &&
                current.status !== 'queued_scan'
              "
              class="progress-capacity"
            >
              {{ progressCapacity }}
            </p>
          </section>
          <div class="task-stats">
            <div>
              <span>文件总数</span
              ><strong
                >{{ current.file_count.toLocaleString()
                }}<small>个</small></strong
              >
            </div>
            <div>
              <span>文件总容量</span
              ><strong>{{ size(current.total_bytes) }}</strong>
            </div>
            <div>
              <span>已处理文件</span
              ><strong
                >{{ current.processed_count.toLocaleString()
                }}<small>个</small></strong
              >
            </div>
            <div>
              <span>内容校验通过</span
              ><strong class="green-text"
                >{{ current.verified_count.toLocaleString()
                }}<small>个</small></strong
              >
            </div>
          </div>
          <section class="panel files-panel">
            <div class="files-toolbar">
              <div>
                <h2>文件清单</h2>
                <span>{{ total.toLocaleString() }} 项</span>
              </div>
              <div class="toolbar-right">
                <label class="search-box"
                  ><Search :size="16" /><input
                    v-model="search"
                    placeholder="搜索文件名或路径"
                    aria-label="搜索文件" /></label
                ><button
                  :class="[
                    'button',
                    problemOnly ? 'selected-button' : 'secondary',
                    'small',
                  ]"
                  @click="problemOnly = !problemOnly"
                >
                  <CircleAlert :size="15" />问题项</button
                ><button
                  v-if="editable && selected.size"
                  class="button secondary small"
                  @click="edit()"
                >
                  <Pencil :size="14" />修改 {{ selected.size }} 项
                </button>
              </div>
            </div>
            <div class="category-tabs">
              <button :class="{ active: filter === '' }" @click="filter = ''">
                全部<span>{{ current.file_count }}</span></button
              ><button
                v-for="(label, key) in labels"
                :key="key"
                :class="{ active: filter === key }"
                @click="filter = String(key)"
              >
                <component :is="icons[key]" :size="14" />{{ label
                }}<span>{{ current.stats?.categories[key] || 0 }}</span>
              </button>
            </div>
            <div class="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th v-if="editable" class="checkbox-cell">
                      <input
                        type="checkbox"
                        :checked="selectedAll"
                        @change="toggleAll"
                        aria-label="选择本页全部文件"
                      />
                    </th>
                    <th>文件名称</th>
                    <th>类型</th>
                    <th>月份</th>
                    <th>地点</th>
                    <th>大小</th>
                    <th>状态</th>
                    <th v-if="editable"></th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="entry in files"
                    :key="entry.id"
                    :class="{ selected: selected.has(entry.id) }"
                  >
                    <td v-if="editable" class="checkbox-cell">
                      <input
                        v-if="entry.kind === 'file'"
                        type="checkbox"
                        :checked="selected.has(entry.id)"
                        @change="
                          selected.has(entry.id)
                            ? selected.delete(entry.id)
                            : selected.add(entry.id)
                        "
                        :aria-label="'选择 ' + name(entry.source_rel)"
                      />
                    </td>
                    <td class="file-name">
                      <div>
                        <span :class="['file-icon', entry.category]"
                          ><component
                            :is="icons[entry.category] || File"
                            :size="18" /></span
                        ><span
                          ><strong :title="entry.source_rel">{{
                            name(entry.source_rel)
                          }}</strong
                          ><small
                            :title="entry.error || entry.target_rel"
                            :class="{ 'error-text': entry.error }"
                            >{{
                              entry.error ||
                              entry.target_rel ||
                              entry.source_rel
                            }}</small
                          ></span
                        ><CircleAlert
                          v-if="entry.warnings?.length"
                          class="warning-icon"
                          :size="15"
                          :title="basis(entry)"
                        /><span
                          v-if="entry.manual"
                          class="manual-tag"
                          title="已人工修正"
                          >改</span
                        >
                      </div>
                    </td>
                    <td>
                      <span :class="['category-label', entry.category]">{{
                        labels[entry.category]
                      }}</span>
                    </td>
                    <td class="numeric" :title="basis(entry)">
                      {{ entry.month || "—" }}
                    </td>
                    <td class="location-cell" :title="basis(entry)">
                      <MapPin
                        v-if="entry.location_source === 'gps_approximate'"
                        :size="12"
                      />{{ place(entry.location) }}
                    </td>
                    <td class="numeric muted">{{ size(entry.size) }}</td>
                    <td>
                      <span :class="['status-label', entry.status]">{{
                        statuses[entry.status] || entry.status
                      }}</span>
                    </td>
                    <td v-if="editable">
                      <button
                        v-if="entry.kind === 'file'"
                        class="icon-button"
                        @click="edit(entry)"
                        :aria-label="'修改 ' + name(entry.source_rel)"
                      >
                        <Pencil :size="15" />
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
              <div v-if="!files.length" class="table-empty">
                <LoaderCircle v-if="busy" class="spin" :size="26" /><Search
                  v-else
                  :size="26"
                />
                <p>{{ busy ? "正在建立文件清单…" : "没有符合条件的文件" }}</p>
              </div>
            </div>
            <div class="pagination">
              <span>每页 50 项 · 共 {{ total.toLocaleString() }} 项</span>
              <div>
                <button
                  class="icon-button"
                  :disabled="filePage <= 1"
                  @click="pageFiles(-1)"
                  aria-label="上一页"
                >
                  <ChevronLeft :size="17" /></button
                ><span>{{ filePage }} / {{ allPages }}</span
                ><button
                  class="icon-button"
                  :disabled="filePage >= allPages"
                  @click="pageFiles(1)"
                  aria-label="下一页"
                >
                  <ChevronRight :size="17" />
                </button>
              </div>
            </div>
          </section>
          <div
            v-if="
              editable ||
              ['paused', 'failed', 'completed_issues'].includes(current.status)
            "
            class="execution-bar"
          >
            <span
              ><ShieldCheck :size="18" />{{
                editable
                  ? "确认分类与目标路径后，开始复制。"
                  : "可重试复制和校验；源目录已变化时，请新建任务重新扫描。"
              }}</span
            >
            <div>
              <select v-model="verification" aria-label="本次校验级别">
                <option
                  v-for="level in levels"
                  :key="level.id"
                  :value="level.id"
                >
                  {{ level.name }}
                </option></select
              ><button
                class="button primary"
                :disabled="loading || !current.file_count"
                @click="showConfirm = true"
              >
                <Play :size="16" />{{
                  editable ? "开始复制并校验" : "继续 / 重试任务"
                }}
              </button>
            </div>
          </div>
        </template>

        <template v-else-if="page === 'history'">
          <div class="page-heading compact">
            <div>
              <div class="eyebrow">EVERY STEP, RECORDED</div>
              <h1>每次整理，都有记录。</h1>
              <p>回看文件去向、校验结果与处理报告。</p>
            </div>
            <button class="button primary" @click="newTask">
              <Plus :size="17" />新建任务
            </button>
          </div>
          <section class="panel history-panel">
            <div v-if="!jobs.length" class="large-empty">
              <History :size="38" />
              <h3>还没有整理任务</h3>
              <p>从选择一个目录开始，建立你的有序媒体库。</p>
              <button class="button primary" @click="newTask">
                创建第一个任务<ArrowRight :size="16" />
              </button>
            </div>
            <button
              v-for="job in jobs"
              :key="job.id"
              class="history-row"
              @click="selectJob(job)"
            >
              <span class="history-icon"><FolderOpen :size="23" /></span>
              <div>
                <strong>{{ name(job.source) }}</strong><small
                  :title="job.source"
                  >{{ job.source }}</small
                >
              </div>
              <div class="history-metrics">
                <strong>{{ job.file_count.toLocaleString() }} 个文件</strong
                ><small>{{ size(job.total_bytes) }}</small>
              </div>
              <div>
                <span :class="['status-label', job.status]">{{
                  statuses[job.status]
                }}</span><small>{{ date(job.created_at) }}</small>
              </div>
              <ChevronRight :size="19" />
            </button>
          </section>
        </template>

        <template v-else-if="page === 'settings'">
          <div class="page-heading compact">
            <div>
              <div class="eyebrow">MAKE IT YOURS</div>
              <h1>规则与设置</h1>
              <p>保存你的整理习惯，网页与命令行共同使用。</p>
            </div>
            <div class="heading-actions">
              <label class="button secondary import-label"
                ><Download :size="16" />导入配置<input
                  type="file"
                  accept=".json"
                  @change="importConfig" /></label
              ><button class="button secondary" @click="exportConfig">
                <Download :size="16" />导出配置
              </button>
            </div>
          </div>
          <div class="settings-grid">
            <section class="panel settings-panel">
              <h2>常用配置</h2>
              <p class="muted">作为新任务的默认值，执行前仍可调整。</p>
              <label class="field-label" for="saved-source">常用源目录</label
              ><input
                id="saved-source"
                class="standard-input"
                v-model="settings.source"
                placeholder="完整目录路径"
              /><label class="field-label" for="saved-target"
                >常用目标目录</label
              ><input
                id="saved-target"
                class="standard-input"
                v-model="settings.target"
                placeholder="完整目录路径"
              /><label class="field-label" for="saved-level">默认校验强度</label
              ><select
                id="saved-level"
                class="standard-input"
                v-model="settings.verification"
              >
                <option
                  v-for="level in levels"
                  :key="level.id"
                  :value="level.id"
                >
                  {{ level.name }}
                </option></select
              ><label class="field-label" for="city-radius"
                >GPS 城市匹配最大距离 <span>公里</span></label
              ><input
                id="city-radius"
                class="standard-input"
                type="number"
                min="1"
                max="500"
                v-model.number="settings.max_city_distance_km"
              />
              <div class="field-hint">
                超过此距离归入其他地点。城市匹配为近似结果，可在预览中修正。
              </div>
              <div class="settings-actions">
                <button class="button primary" @click="saveSettings">
                  <Check :size="16" />保存配置
                </button>
              </div>
            </section>
            <div>
              <section class="panel settings-panel rules-panel">
                <h2>自定义文件类型</h2>
                <p class="muted">在内置常见格式基础上补充或覆盖扩展名规则。</p>
                <div v-if="!rules.length" class="rules-empty">
                  <FileText :size="25" />
                  <p>正在使用内置识别规则</p>
                  <small>照片仍会结合拍摄元数据辅助判断。</small>
                </div>
                <div
                  v-for="(rule, index) in rules"
                  :key="index"
                  class="rule-row"
                >
                  <input
                    v-model="rule.extension"
                    placeholder="扩展名，如 psb"
                    aria-label="扩展名"
                  /><ArrowRight :size="15" /><select
                    v-model="rule.category"
                    aria-label="归档类型"
                  >
                    <option
                      v-for="(label, key) in labels"
                      :key="key"
                      :value="key"
                    >
                      {{ label }}
                    </option></select
                  ><button
                    class="icon-button"
                    @click="rules.splice(index, 1)"
                    aria-label="删除规则"
                  >
                    <X :size="16" />
                  </button>
                </div>
                <button
                  class="text-button add-rule"
                  @click="rules.push({ extension: '', category: 'images' })"
                >
                  <Plus :size="15" />添加识别规则
                </button>
              </section>
              <section class="panel about-panel">
                <div class="about-brand">
                  <span class="brand-icon"><Layers3 :size="21" /></span>
                  <div>
                    <strong>Media Organizer</strong
                    ><small>媒体整理器 · v{{ meta?.version || "0.1.0" }}</small>
                  </div>
                </div>
                <p>本地处理 · 离线运行 · 复制后校验</p>
                <div>
                  <span
                    class="status-dot"
                    :class="{ disconnected: !meta?.places?.available }"
                  ></span
                  >离线城市库 {{ meta?.places?.available ? "已就绪" : "未准备"
                  }}<small v-if="meta?.places?.built_at">{{
                    meta.places.built_at.slice(0, 10)
                  }}</small>
                </div>
                <small
                  >地理数据：GeoNames · CC BY 4.0<br />城市点匹配不等同于严格行政区划归属。</small
                >
              </section>
            </div>
          </div>
        </template>
        <footer class="page-footer">
          <span>MEDIA ORGANIZER</span><span>有序归档，安心留存。</span>
        </footer>
      </main>
    </div>

    <Transition name="toast"
      ><div
        v-if="toast"
        role="status"
        :class="['toast-message', { error: toastError }]"
      >
        <CircleAlert v-if="toastError" :size="18" /><CircleCheck
          v-else
          :size="18"
        />{{ toast
        }}<button @click="toast = ''" aria-label="关闭提示">
          <X :size="15" />
        </button></div
    ></Transition>
    <div v-if="browser" class="modal-backdrop" @click.self="browser = null">
      <section
        class="modal directory-modal"
        role="dialog"
        aria-modal="true"
        aria-label="选择目录"
      >
        <div class="modal-heading">
          <h2>选择{{ browser.kind === "source" ? "源" : "目标" }}目录</h2>
          <button class="icon-button" @click="browser = null" aria-label="关闭">
            <X :size="20" />
          </button>
        </div>
        <form
          class="directory-address"
          @submit.prevent="browse(browser!.kind, browserPath)"
        >
          <button
            type="button"
            class="icon-button"
            @click="browse(browser!.kind, browser!.parent)"
            aria-label="上级目录"
          >
            <ArrowUp :size="17" /></button
          ><input
            v-model="browserPath"
            placeholder="输入目录路径后回车"
            aria-label="目录路径"
          /><button class="icon-button" type="submit" aria-label="打开路径">
            <ArrowRight :size="17" />
          </button>
        </form>
        <div class="directory-list">
          <div v-if="browserBusy" class="table-empty">
            <LoaderCircle class="spin" />
          </div>
          <button
            v-for="directory in browser.directories"
            :key="directory.path"
            :disabled="directory.link"
            @click="browse(browser!.kind, directory.path)"
          >
            <Folder :size="20" /><span>{{ directory.name }}</span
            ><small v-if="directory.link">链接</small
            ><ChevronRight :size="16" />
          </button>
          <p
            v-if="!browserBusy && !browser.directories.length"
            class="directory-empty"
          >
            此目录下没有可进入的子目录
          </p>
        </div>
        <div class="modal-footer">
          <span :title="browser.path">{{
            browser.path || "选择磁盘或主目录"
          }}</span><button
            class="button primary"
            :disabled="!browser.path"
            @click="chooseDirectory"
          >
            选择此目录<Check :size="16" />
          </button>
        </div>
      </section>
    </div>
    <div v-if="showEdit" class="modal-backdrop" @click.self="showEdit = false">
      <section
        class="modal edit-modal"
        role="dialog"
        aria-modal="true"
        aria-label="修改分类"
      >
        <div class="modal-heading">
          <div>
            <h2>修改整理分类</h2>
            <p>已选择 {{ selected.size }} 个文件，留空的项目保持原值。</p>
          </div>
          <button
            class="icon-button"
            @click="showEdit = false"
            aria-label="关闭"
          >
            <X :size="20" />
          </button>
        </div>
        <form @submit.prevent="saveEdit">
          <label class="field-label">类型</label
          ><select v-model="editCategory" class="standard-input">
            <option value="">保持原类型</option>
            <option v-for="(label, key) in labels" :key="key" :value="key">
              {{ label }} / {{ key }}
            </option></select
          ><label class="field-label">月份</label
          ><input
            class="standard-input"
            v-model="editMonth"
            placeholder="YYYYMM，例如 202611"
            pattern="[0-9]{6}"
          /><label class="field-label">地点</label
          ><input
            class="standard-input"
            v-model="editLocation"
            placeholder="例如 CN_Zhejiang_Hangzhou 或 others"
          />
          <div class="verification-hint">
            <ShieldCheck :size="15" />仅调整归档路径，源文件元数据保持原样。
          </div>
          <div class="modal-footer">
            <button
              type="button"
              class="button secondary"
              @click="showEdit = false"
            >
              取消</button
            ><button class="button primary" type="submit">
              <Check :size="16" />更新预览
            </button>
          </div>
        </form>
      </section>
    </div>
    <div
      v-if="showConfirm && current"
      class="modal-backdrop"
      @click.self="showConfirm = false"
    >
      <section
        class="modal confirm-modal"
        role="dialog"
        aria-modal="true"
        aria-label="确认复制"
      >
        <span class="confirm-icon"><ShieldCheck :size="29" /></span>
        <h2>准备开始整理</h2>
        <p>
          将
          {{ current.file_count.toLocaleString() }}
          个文件复制到目标目录，随后进行{{
            levels.find((level) => level.id === verification)?.name
          }}。
        </p>
        <div class="confirm-path">
          <FolderOpen :size="18" /><span>{{ current.target }}</span>
        </div>
        <ul>
          <li><Check :size="15" />保留源文件，目标同名冲突时保留两份</li>
          <li><Check :size="15" />任务进度自动记录，支持中断后继续</li>
          <li><Check :size="15" />结果与对应清单保存在目标目录</li>
        </ul>
        <div class="modal-footer">
          <button class="button secondary" @click="showConfirm = false">
            返回检查</button
          ><button class="button primary" @click="execute()">
            <Play :size="16" />确认复制
          </button>
        </div>
      </section>
    </div>
  </div>
</template>
