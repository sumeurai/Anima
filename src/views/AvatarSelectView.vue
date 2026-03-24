<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { avatarsDelete, avatarsList, getApiBaseUrl, getAvatarById } from "../api/avatars";
import { translate } from "../locales";
import { clearAuthCredentials } from "../api/user";
import { useAppState } from "../stores/appState";
import { DEFAULT_USER_EMAIL, PUBLIC_AVATAR_IDS, PUBLIC_AVATAR_NAMES } from "../stores/appState";
import type { Avatar } from "../stores/appState";

const router = useRouter();
const route = useRoute();
const { state, selectAvatar, syncAvatarsFromServer } =
  useAppState();
const activeTab = ref<"all" | "official" | "custom">("all");
const sseConnections = new Map<string, EventSource>();
const sseRetryTimers = new Map<string, number>();
const listLoading = ref(false);
const deletingAvatarId = ref("");
const showDeleteDialog = ref(false);
const pendingDeleteAvatar = ref<{ id: string; name: string } | null>(null);
const deleteErrorMessage = ref("");

const statusMap: Record<string, string> = {
  "1": "check",
  "2": "style-completed",
  "3": "model-completed",
  "42": "style-error",
  "43": "model-error",
};

const getSseUserId = () => {
  try {
    const raw = localStorage.getItem("userStore");
    if (!raw) return "";
    const parsed = JSON.parse(raw) as { userData?: { id?: string | number } };
    return String(parsed?.userData?.id ?? "");
  } catch {
    return "";
  }
};

const visibleAvatars = computed(() => {
  if (activeTab.value === "all") return state.avatars;
  if (activeTab.value === "official") return state.avatars.filter((item) => item.type === "official" || item.type === "public");
  return state.avatars.filter((item) => item.type === activeTab.value);
});

const renderedAvatars = computed(() => visibleAvatars.value.filter((item) => item.id !== "sumi"));

const publicCount = computed(() => state.avatars.filter((item) => item.type === "public").length);
const officialCount = computed(() => state.avatars.filter((item) => item.type === "official").length + publicCount.value);
const customCount = computed(() => state.avatars.filter((item) => item.type === "custom").length);

const isAvatarReady = (avatar: { generating?: boolean; desc?: string }) => {
  if (avatar.generating) return false;
  return true;
};

const handleAvatarCardClick = (avatar: Avatar) => {
  if (avatar.generating) return;
  selectAvatar(avatar.id);
  if (isAvatarReady(avatar)) {
    const query: Record<string, string> = {};
    if (avatar.type === "public") query.mode = "public";
    router.push({ path: "/chat", query });
  }
};

const isSuccessCode = (code?: number) => code === 200 || code === 0;

const fetchPublicAvatars = async () => {
  const alreadyLoaded = state.avatars.filter((a) => a.type === "public").map((a) => a.id);
  const toFetch = PUBLIC_AVATAR_IDS.filter((id) => !alreadyLoaded.includes(id));
  if (!toFetch.length) return;
  await Promise.all(
    toFetch.map(async (avatarId) => {
      try {
        const res = await getAvatarById(avatarId);
        if (!isSuccessCode(res?.code) || !res?.data) return;
        const d = res.data as Record<string, any>;
        const avatar: Avatar = {
          id: avatarId,
          name: PUBLIC_AVATAR_NAMES[avatarId] || d.nickname || d.name || "Avatar",
          desc: translate("store.defaultAvatarDesc"),
          type: "public",
          image: d.avatarImg || d.image || d.lookImg || "/sumi.png",
          modelUrl: d.downloadLink || d.modelUrl || "",
          modelId: d.modelId || "",
          voiceId: String(d.voiceId ?? d.ttsId ?? d.defaultVoiceId ?? ""),
        };
        const exists = state.avatars.find((a) => a.id === avatarId);
        if (!exists) {
          const firstCustomIdx = state.avatars.findIndex((a) => a.type === "custom");
          if (firstCustomIdx >= 0) {
            state.avatars.splice(firstCustomIdx, 0, avatar);
          } else {
            state.avatars.push(avatar);
          }
        }
      } catch {
        // Ignore individual public avatar fetch failures.
      }
    }),
  );
};

const fetchAvatarsList = async () => {
  listLoading.value = true;
  try {
    const res = await avatarsList({
      orderByColumn: "open_time",
      isAsc: "desc",
      nickname: "",
    });
    if (!isSuccessCode(res?.code)) return;
    const list = res?.data?.userAvatarsList || res?.data?.list || [];
    syncAvatarsFromServer(list);
  } catch {
    // Keep local list as fallback if request fails.
  } finally {
    listLoading.value = false;
  }
};

const openDeleteDialog = (avatar: { id: string; name: string; type: string; generating?: boolean }) => {
  if (avatar.type !== "custom") return;
  pendingDeleteAvatar.value = {
    id: avatar.id,
    name: avatar.name || translate("select.thisAvatar"),
  };
  deleteErrorMessage.value = "";
  showDeleteDialog.value = true;
};

const closeDeleteDialogInternal = (force = false) => {
  if (!force && deletingAvatarId.value) return;
  showDeleteDialog.value = false;
  pendingDeleteAvatar.value = null;
  deleteErrorMessage.value = "";
};

const closeDeleteDialog = () => {
  closeDeleteDialogInternal(false);
};

const confirmDeleteAvatar = async () => {
  if (!pendingDeleteAvatar.value) return;
  const { id } = pendingDeleteAvatar.value;
  deletingAvatarId.value = id;
  deleteErrorMessage.value = "";
  try {
    closeSseConnection(id);
    const res = await avatarsDelete(id);
    if (!isSuccessCode(res?.code)) {
      deleteErrorMessage.value = res?.msg || translate("select.deleteFailed");
      return;
    }
    await fetchAvatarsList();
    closeDeleteDialogInternal(true);
  } catch {
    deleteErrorMessage.value = translate("select.deleteFailed");
  } finally {
    deletingAvatarId.value = "";
  }
};

const logoutForTest = () => {
  closeAllSseConnections();
  clearAuthCredentials();
  localStorage.removeItem("userStore");
  state.userEmail = DEFAULT_USER_EMAIL;
  router.push("/login");
};

const clearRetry = (avatarId: string) => {
  const timer = sseRetryTimers.get(avatarId);
  if (timer) {
    window.clearTimeout(timer);
    sseRetryTimers.delete(avatarId);
  }
};

const closeSseConnection = (avatarId: string) => {
  clearRetry(avatarId);
  const current = sseConnections.get(avatarId);
  if (current) {
    current.close();
    sseConnections.delete(avatarId);
  }
};

const closeAllSseConnections = () => {
  Array.from(sseConnections.keys()).forEach((id) => closeSseConnection(id));
};

const scheduleReconnect = (avatarId: string) => {
  if (!state.avatars.find((item) => item.id === avatarId && item.generating)) return;
  clearRetry(avatarId);
  const timer = window.setTimeout(() => {
    connectSseForAvatar(avatarId);
  }, 5000);
  sseRetryTimers.set(avatarId, timer);
};

const handleSseMessage = async (avatarId: string, payload: any) => {
  const rawEvent = payload?.event;
  const eventName = statusMap[String(rawEvent)] ?? String(rawEvent ?? "");
  if (eventName === "model-completed") {
    closeSseConnection(avatarId);
    await fetchAvatarsList();
    return;
  }
  if (eventName === "model-error" || eventName === "style-error") {
    closeSseConnection(avatarId);
    await fetchAvatarsList();
  }
};

const connectSseForAvatar = (avatarId: string) => {
  if (sseConnections.has(avatarId)) return;
  const userId = getSseUserId();
  if (!userId) return;
  const url = `${getApiBaseUrl()}/web-api/portrait/sse/connect/${userId}?businessId=${avatarId}`;
  const eventSource = new EventSource(url);

  eventSource.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      const payloadBusinessId = payload?.businessId ?? payload?.data?.businessId;
      if (payloadBusinessId && String(payloadBusinessId) !== avatarId) return;
      handleSseMessage(avatarId, payload);
    } catch {
      // Ignore malformed SSE payloads.
    }
  };

  eventSource.onerror = () => {
    closeSseConnection(avatarId);
    scheduleReconnect(avatarId);
  };

  sseConnections.set(avatarId, eventSource);
};

const syncSseConnections = () => {
  const generatingIds = new Set(
    state.avatars.filter((item) => item.type === "custom" && item.generating).map((item) => item.id),
  );

  generatingIds.forEach((id) => connectSseForAvatar(id));
  Array.from(sseConnections.keys())
    .filter((id) => !generatingIds.has(id))
    .forEach((id) => closeSseConnection(id));
};

watch(
  () => state.avatars.map((item) => `${item.id}:${item.generating ? 1 : 0}`).join("|"),
  () => {
    syncSseConnections();
  },
  { immediate: true },
);

watch(
  () => route.query.refresh,
  () => {
    fetchAvatarsList();
  },
);

onBeforeUnmount(() => {
  closeAllSseConnections();
});

onMounted(() => {
  fetchPublicAvatars();
  fetchAvatarsList();
});
</script>

<template>
  <section class="page select-page-root">
    <header class="select-header">
      <div class="select-header-left">
        <h1>{{ translate("select.title") }}</h1>
        <p>{{ listLoading ? translate("select.syncing") : translate("select.subtitle") }}</p>
      </div>
      <div class="select-header-right">
        <button class="test-logout-btn" @click="logoutForTest">{{ translate("select.logout") }}</button>
        <div class="user-badge">
          <div class="user-avatar">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
              <circle cx="12" cy="7" r="4" />
            </svg>
          </div>
          <span class="user-email">{{ state.userEmail }}</span>
        </div>
      </div>
    </header>

    <div class="select-tabs">
      <button class="select-tab" :class="{ active: activeTab === 'all' }" @click="activeTab = 'all'">
        {{ translate("select.tabAll") }} <span class="count">{{ state.avatars.length }}</span>
      </button>
      <button
        class="select-tab"
        :class="{ active: activeTab === 'official' }"
        @click="activeTab = 'official'"
      >
        {{ translate("select.tabOfficial") }} <span class="count">{{ officialCount }}</span>
      </button>
      <button class="select-tab" :class="{ active: activeTab === 'custom' }" @click="activeTab = 'custom'">
        {{ translate("select.tabMine") }} <span class="count">{{ customCount }}</span>
      </button>
    </div>

    <div class="select-content">
      <div class="char-grid">
        <button class="char-card create-new" @click="router.push('/avatars/create')">
          <div class="create-new-inner">
            <div class="create-plus">＋</div>
            <span class="create-text">{{ translate("select.createNew") }}</span>
            <span class="create-subtitle">{{ translate("select.createDesc") }}</span>
          </div>
        </button>

        <button
          v-for="avatar in renderedAvatars"
          :key="avatar.id"
          class="char-card"
          :class="{ selected: avatar.id === state.selectedAvatarId, generating: avatar.generating }"
          @click="handleAvatarCardClick(avatar)"
        >
          <div class="char-preview">
            <button
              v-if="avatar.type === 'custom'"
              class="char-delete-btn"
              :disabled="deletingAvatarId === avatar.id"
              @click.stop="openDeleteDialog(avatar)"
            >
              {{ deletingAvatarId === avatar.id ? translate("select.deleting") : translate("select.delete") }}
            </button>
            <img :src="avatar.image" :alt="avatar.name" />
            <div v-if="avatar.generating" class="gen-card-overlay">
              <div class="gen-card-spinner"></div>
              <span class="gen-card-label">
                {{
                  avatar.progress && avatar.progress > 0
                    ? translate("select.generatingProgress", { progress: avatar.progress })
                    : translate("select.generating")
                }}
              </span>
              <div class="gen-card-progress">
                <div class="gen-card-progress-fill" :style="{ width: `${avatar.progress ?? 0}%` }"></div>
              </div>
            </div>
          </div>
          <div class="char-info">
            <div class="char-name">{{ avatar.name }}</div>
            <div class="char-desc">
              {{ avatar.generating ? translate("select.estimate") : avatar.desc }}
            </div>
            <span class="char-tag">{{ avatar.type === "public" ? translate("select.publicTag") : avatar.type === "official" ? translate("select.officialTag") : translate("select.customTag") }}</span>
          </div>
        </button>
      </div>
    </div>

    <!-- <footer class="select-footer">
      <span class="selected-info">{{ translate("select.selected") }}: <strong>{{ selectedAvatar()?.name ?? "-" }}</strong></span>
      <button class="confirm-btn" :disabled="!canStart" @click="goChat">
        {{ translate("select.startChat") }}
      </button>
    </footer> -->

    <div class="confirm-overlay" :class="{ open: showDeleteDialog }" @click="closeDeleteDialog">
      <div class="confirm-card" @click.stop>
        <h3>{{ translate("select.deleteAvatarTitle") }}</h3>
        <p>{{ translate("select.deleteAvatarDesc", { name: pendingDeleteAvatar?.name || "-" }) }}</p>
        <p v-if="deleteErrorMessage" class="confirm-error">{{ deleteErrorMessage }}</p>
        <div class="confirm-actions">
          <button class="confirm-cancel-btn" :disabled="Boolean(deletingAvatarId)" @click="closeDeleteDialog">{{ translate("common.cancel") }}</button>
          <button class="confirm-danger-btn" :disabled="Boolean(deletingAvatarId)" @click="confirmDeleteAvatar">
            {{ deletingAvatarId ? translate("select.deleting") : translate("common.confirmDelete") }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
