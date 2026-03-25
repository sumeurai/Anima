<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AvatarJS from "../utils/atf/AvatarJS";
import { asrBase64, atfDt, audioLanguageList, audioList, getApiBaseUrl, getAvatarById, ttsTransform, ttsUpdate } from "../api/avatars";
import { openclawChat, fetchOpenClawAgents, syncOpenClawAgents } from "../api/chat";
import type { ChatMessage as OpenClawMessage, OpenClawAgent } from "../api/chat";
import { translate } from "../locales";
import { DEFAULT_AVATAR_VOICE_ID, PUBLIC_AVATAR_IDS, PUBLIC_AVATAR_NAMES, useAppState } from "../stores/appState";

const router = useRouter();
const route = useRoute();
const { state, selectedAvatar, pushUserMessage, pushAgentMessage } = useAppState();
const isPublicMode = computed(() => route.query.mode === "public");

const input = ref("");
const mode = ref<"text" | "voice">("text");
const bubbleText = ref("");
const bubbleVisible = ref(true);
const isTyping = ref(false);
const isRecording = ref(false);

const showHistoryPanel = ref(false);
const showVoicePanel = ref(false);
const showAgentPanel = ref(false);
const showAgentDetail = ref(false);
const historyListRef = ref<HTMLElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);
const avatarRenderer = ref<AvatarJS | null>(null);
const modelUrl = ref("");
const isBroadcast = ref(false);
const latestAgentText = ref("");
const mediaRecorder = ref<MediaRecorder | null>(null);
const mediaStream = ref<MediaStream | null>(null);
const recordedChunks = ref<Blob[]>([]);
const isAvatarLoading = ref(true);
const isChatMode = ref(false);
const fallbackImageLoaded = ref(false);
const avatarLoadTimeout = ref<number | null>(null);
const avatarLoadingStartedAt = ref(0);
const avatarLoadingFinishTimer = ref<number | null>(null);
const voiceOptions = ref<
  Array<{
    id: string;
    otherId?: string | number;
    languageId?: string | number;
    lang: string;
    group: string;
    name: string;
    desc: string;
    gender: "female" | "male";
  }>
>([]);
const voiceLoading = ref(false);
const voiceSavingId = ref("");
const chatAbortController = ref<AbortController | null>(null);
const chatHistory = ref<OpenClawMessage[]>([]);

const fallbackVoices = computed(
  () =>
    [
      {
        id: "zh-female",
        lang: translate("chat.voices.langZh"),
        group: translate("chat.voices.groupZh"),
        name: translate("chat.voices.zhFemaleName"),
        desc: translate("chat.voices.zhFemaleDesc"),
        gender: "female",
      },
      {
        id: "zh-male",
        lang: translate("chat.voices.langZh"),
        group: translate("chat.voices.groupZh"),
        name: translate("chat.voices.zhMaleName"),
        desc: translate("chat.voices.zhMaleDesc"),
        gender: "male",
      },
      {
        id: "en-female",
        lang: translate("chat.voices.langEn"),
        group: translate("chat.voices.groupEn"),
        name: translate("chat.voices.enFemaleName"),
        desc: translate("chat.voices.enFemaleDesc"),
        gender: "female",
      },
      {
        id: "en-male",
        lang: translate("chat.voices.langEn"),
        group: translate("chat.voices.groupEn"),
        name: translate("chat.voices.enMaleName"),
        desc: translate("chat.voices.enMaleDesc"),
        gender: "male",
      },
      {
        id: "ja-female",
        lang: translate("chat.voices.langJa"),
        group: translate("chat.voices.groupJa"),
        name: translate("chat.voices.jaFemaleName"),
        desc: translate("chat.voices.jaFemaleDesc"),
        gender: "female",
      },
      {
        id: "ja-male",
        lang: translate("chat.voices.langJa"),
        group: translate("chat.voices.groupJa"),
        name: translate("chat.voices.jaMaleName"),
        desc: translate("chat.voices.jaMaleDesc"),
        gender: "male",
      },
    ] as const,
);
const voices = computed(() => (voiceOptions.value.length ? voiceOptions.value : fallbackVoices.value));
const selectedVoice = ref(DEFAULT_AVATAR_VOICE_ID);
const voiceGroups = computed(() => {
  const groupMap = new Map<string, any[]>();
  voices.value.forEach((voice) => {
    const existing = groupMap.get(voice.group) ?? [];
    groupMap.set(voice.group, [...existing, voice]);
  });
  return Array.from(groupMap.entries()).map(([group, items]) => ({ group, items }));
});

type AgentItem = {
  id: string;
  name: string;
  type: string;
  desc: string;
  persona: string;
};
const agents = ref<AgentItem[]>([]);
const agentsLoading = ref(false);
const selectedAgentId = ref("");

const applyAgentList = (list: OpenClawAgent[]) => {
  agents.value = list.map((a: OpenClawAgent) => ({
    id: a.id,
    name: a.identityName || a.name || a.id,
    type: a.isDefault ? "Official" : "Custom",
    desc: a.owned_by || "OpenClaw Agent",
    persona: a.identityName
      ? `${a.identityEmoji || ""} ${a.identityName}`.trim()
      : a.name || a.id,
  }));
  if (agents.value.length && !selectedAgentId.value) {
    const avatar = currentAvatar.value;
    const bound = avatar?.agentId;
    if (bound && agents.value.some((a) => a.id === bound)) {
      selectedAgentId.value = bound;
    } else {
      const def = agents.value.find((a) => a.type === "Official");
      selectedAgentId.value = def?.id || agents.value[0].id;
    }
  }
};

const loadAgents = async () => {
  agentsLoading.value = true;
  try {
    applyAgentList(await fetchOpenClawAgents());
  } finally {
    agentsLoading.value = false;
  }
};

const syncAgents = async () => {
  agentsLoading.value = true;
  try {
    applyAgentList(await syncOpenClawAgents());
  } finally {
    agentsLoading.value = false;
  }
};

const avatarName = computed(() => selectedAvatar()?.name ?? "Agent");
const currentAvatar = computed(() => selectedAvatar());
const useCanvasRenderer = computed(() => Boolean(modelUrl.value));
const resolvedVoiceId = computed(() => {
  const voiceId = String(currentAvatar.value?.voiceId ?? "").trim();
  return voiceId || selectedVoice.value || DEFAULT_AVATAR_VOICE_ID;
});
const resolvedTtsVoiceId = computed(() => {
  const vid = resolvedVoiceId.value;
  const match = voiceOptions.value.find((v) => v.id === vid);
  const otherId = match?.otherId;
  if (otherId !== undefined && otherId !== null && String(otherId).trim()) {
    return Number(otherId) || 1;
  }
  return Number(vid) || 1;
});
const EMPTY_AGENT: AgentItem = { id: "", name: "Agent", type: "", desc: "", persona: "" };
const currentAgent = computed(
  () => agents.value.find((item) => item.id === selectedAgentId.value) ?? agents.value[0] ?? EMPTY_AGENT,
);
const showSpeechBubble = computed(() => isChatMode.value && !isAvatarLoading.value && bubbleVisible.value);
const waitingAvatarImage = computed(() => {
  const avatar = currentAvatar.value;
  if (!avatar || avatar.id === "sumi") return "";
  const src = String(avatar.image || "").trim();
  return src;
});
const isSuccessCode = (code?: number) => code === 0 || code === 200;

const updateCurrentAvatarVoice = (voiceId: string) => {
  const normalized = String(voiceId || "").trim() || DEFAULT_AVATAR_VOICE_ID;
  const avatar = currentAvatar.value;
  if (!avatar) return;
  avatar.voiceId = normalized;
};

const applyVoiceSelectionLocal = (voiceId: string) => {
  const normalized = String(voiceId || "").trim() || DEFAULT_AVATAR_VOICE_ID;
  selectedVoice.value = normalized;
  updateCurrentAvatarVoice(normalized);
};

const loadVoiceOptions = async (preferredVoiceId?: string) => {
  voiceLoading.value = true;
  try {
    const languageRes = await audioLanguageList();
    if (!isSuccessCode(languageRes?.code) || !Array.isArray(languageRes?.data)) {
      voiceOptions.value = [];
      if (preferredVoiceId) applyVoiceSelectionLocal(preferredVoiceId);
      return;
    }
    const languages = languageRes.data;
    const voiceListResults = await Promise.all(
      languages.map(async (language) => {
        try {
          const listRes = await audioList({ languageId: language?.id });
          return { language, listRes };
        } catch {
          return { language, listRes: undefined };
        }
      }),
    );

    const nextOptions: Array<{
      id: string;
      otherId?: string | number;
      languageId?: string | number;
      lang: string;
      group: string;
      name: string;
      desc: string;
      gender: "female" | "male";
    }> = [];

    voiceListResults.forEach(({ language, listRes }) => {
      if (!isSuccessCode(listRes?.code) || !Array.isArray(listRes?.data)) return;
      listRes.data.forEach((item) => {
        const id = String(item?.id ?? item?.ttsId ?? "").trim();
        if (!id) return;
        const rawSex = String(item?.sex ?? item?.gender ?? "").toLowerCase();
        const gender = rawSex === "1" || rawSex === "female" || rawSex === "f" ? "female" : "male";
        nextOptions.push({
          id,
          otherId: item?.otherId as string | number | undefined,
          languageId: (language?.id as string | number | undefined) ?? (item?.languageId as string | number | undefined),
          lang: String(language?.code ?? item?.languageCode ?? "").toUpperCase(),
          group: String(language?.name ?? item?.languageName ?? translate("chat.voiceTitle")),
          name: String(item?.audioName ?? item?.ttsName ?? id),
          desc: String(item?.desc ?? item?.description ?? ""),
          gender,
        });
      });
    });

    const unique = new Map<string, (typeof nextOptions)[number]>();
    nextOptions.forEach((item) => {
      if (!unique.has(item.id)) unique.set(item.id, item);
    });
    voiceOptions.value = Array.from(unique.values());

    const preferred = String(preferredVoiceId || "").trim();
    if (preferred && voiceOptions.value.some((item) => item.id === preferred)) {
      applyVoiceSelectionLocal(preferred);
      return;
    }
    const fromAvatar = String(currentAvatar.value?.voiceId ?? "").trim();
    if (fromAvatar && voiceOptions.value.some((item) => item.id === fromAvatar)) {
      applyVoiceSelectionLocal(fromAvatar);
      return;
    }
    if (voiceOptions.value.length > 0) {
      applyVoiceSelectionLocal(voiceOptions.value[0].id);
    }
  } finally {
    voiceLoading.value = false;
  }
};

const applyVoiceSelection = async (voice: { id: string; languageId?: string | number } | string) => {
  if (typeof voice === "string") {
    applyVoiceSelectionLocal(voice);
    return;
  }
  const normalized = String(voice?.id || "").trim() || DEFAULT_AVATAR_VOICE_ID;
  const avatarId = String(currentAvatar.value?.id ?? "").trim();
  const languageId = voice?.languageId;
  if (!avatarId || avatarId === "sumi" || languageId === undefined || languageId === null || String(languageId).trim() === "") {
    applyVoiceSelectionLocal(normalized);
    return;
  }
  if (voiceSavingId.value === normalized) return;
  voiceSavingId.value = normalized;
  try {
    const res = await ttsUpdate({
      avatarsId: avatarId,
      languageId,
      ttsId: normalized,
      action: "add",
    });
    if (isSuccessCode(res?.code)) {
      applyVoiceSelectionLocal(normalized);
    }
  } catch {
    // keep previous voice if save failed
  } finally {
    voiceSavingId.value = "";
  }
};

const closePanels = () => {
  showHistoryPanel.value = false;
  showVoicePanel.value = false;
  showAgentPanel.value = false;
  showAgentDetail.value = false;
};

const togglePanel = (type: "history" | "voice" | "agent") => {
  const next = {
    history: !showHistoryPanel.value,
    voice: !showVoicePanel.value,
    agent: !showAgentPanel.value,
  };
  showHistoryPanel.value = false;
  showVoicePanel.value = false;
  showAgentPanel.value = false;
  if (type === "history") showHistoryPanel.value = next.history;
  if (type === "voice") showVoicePanel.value = next.voice;
  if (type === "agent") {
    showAgentPanel.value = next.agent;
    if (next.agent && !agents.value.length && !agentsLoading.value) loadAgents();
  }
};

const scrollHistoryToBottom = () => {
  const el = historyListRef.value;
  if (!el) return;
  el.scrollTop = el.scrollHeight;
};

const assistantReplies = computed(() => [
  translate("chat.assistantReply1"),
  translate("chat.assistantReply2"),
  translate("chat.assistantReply3"),
  translate("chat.assistantReply4"),
]);

const clearAvatarLoadTimeout = () => {
  if (avatarLoadTimeout.value) {
    window.clearTimeout(avatarLoadTimeout.value);
    avatarLoadTimeout.value = null;
  }
};

const clearAvatarFinishTimer = () => {
  if (avatarLoadingFinishTimer.value) {
    window.clearTimeout(avatarLoadingFinishTimer.value);
    avatarLoadingFinishTimer.value = null;
  }
};

const startAvatarLoading = () => {
  isAvatarLoading.value = true;
  isChatMode.value = false;
  avatarLoadingStartedAt.value = Date.now();
  clearAvatarFinishTimer();
  clearAvatarLoadTimeout();
  avatarLoadTimeout.value = window.setTimeout(() => {
    console.warn("[AvatarLoad] timeout reached, forcing exit loading state");
    isAvatarLoading.value = false;
    if (!isChatMode.value) {
      isChatMode.value = true;
    }
    avatarLoadTimeout.value = null;
  }, 20000);
};

const finishAvatarLoading = () => {
  const elapsed = Date.now() - avatarLoadingStartedAt.value;
  const remain = Math.max(500 - elapsed, 0);
  clearAvatarFinishTimer();
  avatarLoadingFinishTimer.value = window.setTimeout(() => {
    isAvatarLoading.value = false;
    avatarLoadingFinishTimer.value = null;
    clearAvatarLoadTimeout();
  }, remain);
};

let _typewriterTimer: number | null = null;
let _typewriterFullText = "";

const stopTypewriter = (applyFull = false) => {
  if (_typewriterTimer !== null) {
    window.clearInterval(_typewriterTimer);
    _typewriterTimer = null;
  }
  if (applyFull && _typewriterFullText) {
    bubbleText.value = _typewriterFullText;
  }
  _typewriterFullText = "";
};

const startTypewriter = (text: string, durationMs: number) => {
  stopTypewriter();
  if (!text) { bubbleText.value = ""; return; }

  const chars = Array.from(text);
  const total = chars.length;
  const interval = Math.max(Math.floor(durationMs / total), 30);
  let idx = 0;

  _typewriterFullText = text;
  bubbleText.value = "";

  _typewriterTimer = window.setInterval(() => {
    idx++;
    bubbleText.value = chars.slice(0, idx).join("");
    if (idx >= total) stopTypewriter();
  }, interval);
};

const handleFallbackReady = () => {
  fallbackImageLoaded.value = true;
  if (!useCanvasRenderer.value) {
    isChatMode.value = true;
    //finishAvatarLoading();
  }
};

const resolveAgentDisplayName = async (): Promise<string> => {
  const avatar = currentAvatar.value;
  const boundAgentId = avatar?.agentId;
  console.log("[AgentName] boundAgentId:", boundAgentId, "avatarName:", avatarName.value);
  try {
    const list = await fetchOpenClawAgents();
    console.log("[AgentName] list:", JSON.stringify(list));
    applyAgentList(list);
    const match = list.find((a) => a.id === boundAgentId)
      || list.find((a) => a.id === selectedAgentId.value)
      || list.find((a) => a.isDefault)
      || list[0];
    console.log("[AgentName] match:", JSON.stringify(match));
    const resolved = match?.identityName || match?.name || match?.id || "";
    if (resolved) return resolved;
  } catch (err) {
    console.error("[AgentName] error:", err);
  }
  return avatarName.value || "Agent";
};

onMounted(async () => {
  const name = await resolveAgentDisplayName();
  bubbleText.value = translate("chat.fallbackBubble", { name });
  if (state.chatMessages.length === 0) {
    pushAgentMessage(translate("chat.welcome", { name }));
  }
  loadAndRenderAvatar();
});

onBeforeUnmount(() => {
  clearAvatarLoadTimeout();
  clearAvatarFinishTimer();
  stopTypewriter();
  stopAcknowledgment();
  stopRecordingOnly();
  chatAbortController.value?.abort();
  avatarRenderer.value?.stopPlay();
  avatarRenderer.value?.close();
  avatarRenderer.value = null;
});

let _avatarLoadActive = false;
watch(
  () => currentAvatar.value?.id,
  () => {
    if (_avatarLoadActive) return;
    applyVoiceSelection(String(currentAvatar.value?.voiceId ?? DEFAULT_AVATAR_VOICE_ID));
    loadAndRenderAvatar();
  },
);

watch(showHistoryPanel, (open) => {
  if (!open) return;
  nextTick(() => {
    scrollHistoryToBottom();
  });
});

watch(
  () => state.chatMessages.length,
  () => {
    if (!showHistoryPanel.value) return;
    nextTick(() => {
      scrollHistoryToBottom();
    });
  },
);

let creatingRenderer = false;
const createRenderer = async () => {
  if (creatingRenderer) return;
  creatingRenderer = true;
  try {
    await _createRendererInner();
  } finally {
    creatingRenderer = false;
  }
};
const _createRendererInner = async () => {
  await nextTick();
  if (!useCanvasRenderer.value || !canvasRef.value) return;
  avatarRenderer.value?.close();

  const old = canvasRef.value;
  try {
    old.width = old.offsetWidth;
  } catch {
    const fresh = document.createElement("canvas");
    fresh.className = old.className;
    fresh.style.cssText = old.style.cssText;
    old.parentNode?.replaceChild(fresh, old);
    canvasRef.value = fresh;
  }

  avatarRenderer.value = new AvatarJS(
    { canvas: canvasRef.value },
    () => {
      isChatMode.value = true;
      finishAvatarLoading();
    },
    () => {
      avatarRenderer.value?.startPlay2();
    },
    () => {},
    (err: unknown) => {
      console.error("[AvatarJS] error:", err);
      avatarRenderer.value?.close();
      avatarRenderer.value = null;
      modelUrl.value = "";
      isChatMode.value = true;
      finishAvatarLoading();
    },
    modelUrl.value,
  );
};

const loadAndRenderAvatar = async () => {
  _avatarLoadActive = true;
  try {
    startAvatarLoading();

    const storedId = state.selectedAvatarId;
    if (storedId && storedId !== "sumi" && !state.avatars.find((a) => a.id === storedId)) {
      try {
        const res = await getAvatarById(storedId);
        if ((res?.code === 200 || res?.code === 0) && res?.data) {
          const d = res.data as Record<string, any>;
          const isPublic = PUBLIC_AVATAR_IDS.includes(storedId);
          state.avatars.push({
            id: storedId,
            name: PUBLIC_AVATAR_NAMES[storedId] || d.nickname || d.name || "Avatar",
            desc: "",
            type: isPublic ? "public" : "custom",
            image: d.avatarImg || d.image || d.lookImg || "",
            modelUrl: d.downloadLink || d.modelUrl || "",
            modelId: d.modelId || "",
            voiceId: String(d.voiceId ?? d.ttsId ?? d.defaultVoiceId ?? ""),
            agentId: d.agentId || undefined,
          });
        }
      } catch { /* ignore */ }
    }

    const avatar = currentAvatar.value;
    modelUrl.value = avatar?.modelUrl || "";
    if (!avatar?.id || avatar.id === "sumi") {
      avatarRenderer.value?.close();
      avatarRenderer.value = null;
      isChatMode.value = true;
      finishAvatarLoading();
      return;
    }
    try {
      const res = await getAvatarById(avatar.id);
      if (res?.code === 200 || res?.code === 0) {
        const data = res.data as
          | {
              downloadLink?: string;
              modelUrl?: string;
              modelId?: string;
              voiceId?: string | number;
              ttsId?: string | number;
              defaultVoiceId?: string | number;
              builtinTts?: Array<{ ttsId?: string | number }>;
              tts?: Array<{ ttsId?: string | number }>;
            }
          | undefined;
        modelUrl.value = data?.downloadLink || data?.modelUrl || modelUrl.value;
        if (data?.modelId && avatar) {
          avatar.modelId = data.modelId;
        }
        const voiceFromBuiltin = data?.builtinTts?.find((item) => item?.ttsId)?.ttsId;
        const voiceFromTts = data?.tts?.find((item) => item?.ttsId)?.ttsId;
        const nextVoiceId = String(
          data?.voiceId ??
            data?.ttsId ??
            data?.defaultVoiceId ??
            voiceFromBuiltin ??
            voiceFromTts ??
            currentAvatar.value?.voiceId ??
            DEFAULT_AVATAR_VOICE_ID,
        ).trim();
        applyVoiceSelectionLocal(nextVoiceId || DEFAULT_AVATAR_VOICE_ID);
        await loadVoiceOptions(nextVoiceId || DEFAULT_AVATAR_VOICE_ID);
      }
    } catch {
      const localVoice = String(currentAvatar.value?.voiceId ?? DEFAULT_AVATAR_VOICE_ID);
      applyVoiceSelectionLocal(localVoice);
      await loadVoiceOptions(localVoice);
    }
    if (!modelUrl.value) {
      avatarRenderer.value?.close();
      avatarRenderer.value = null;
      finishAvatarLoading();
      setTimeout(() => {
        isChatMode.value = true;
      }, 1500);
      return;
    }
    await createRenderer();
    if (useCanvasRenderer.value && !avatarRenderer.value) {
      await nextTick();
      await createRenderer();
    }
  } finally {
    _avatarLoadActive = false;
  }
};

const stopRecordingOnly = () => {
  if (mediaRecorder.value && mediaRecorder.value.state !== "inactive") {
    mediaRecorder.value.stop();
  }
  mediaRecorder.value = null;
  if (mediaStream.value) {
    mediaStream.value.getTracks().forEach((track) => track.stop());
    mediaStream.value = null;
  }
};

const stopPlaying = () => {
  stopTypewriter(true);
  stopAcknowledgment();
  isBroadcast.value = false;
  isTyping.value = false;
  avatarRenderer.value?.stopPlay();
};

const ACK_PHRASES = [
  "好的，收到",
  "好的，让我想想",
  "嗯，稍等一下",
  "好的，马上",
  "收到，我看看",
];

let _ackAborted = false;
let _ackStopTimer: number | null = null;

const playAcknowledgment = () => {
  _ackAborted = false;
  const phrase = ACK_PHRASES[Math.floor(Math.random() * ACK_PHRASES.length)];
  console.log(`[Ack] phrase="${phrase}"`);

  const avatar = currentAvatar.value;
  if (!avatar || !avatarRenderer.value || !avatar.modelId) {
    console.log(`[Ack] skip: avatar=${!!avatar} renderer=${!!avatarRenderer.value} modelId=${avatar?.modelId || "(empty)"}`);
    return;
  }

  (async () => {
    try {
      const voiceId = resolvedTtsVoiceId.value;
      console.log(`[Ack] TTS phrase="${phrase}" voiceId=${voiceId}`);
      const ttsRes = await ttsTransform({ content: phrase, voiceId });
      if (_ackAborted) { console.log("[Ack] aborted after TTS"); return; }

      const audioBase64 = ttsRes?.data?.audioBase64 || "";
      const atfModelId = avatar.modelId || "";
      if (!audioBase64 || !avatarRenderer.value || !atfModelId) {
        console.log("[Ack] skip lip-sync: no audio or no renderer");
        return;
      }

      const traceId = typeof crypto?.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

      const atfRes = await atfDt({
        status: "start",
        dialogueBase64: audioBase64,
        lastDialogueBase64: "",
        modelId: atfModelId,
        traceId,
      });
      if (_ackAborted) { console.log("[Ack] aborted after ATF"); return; }

      if (atfRes?.data && avatarRenderer.value) {
        const audioLen = ttsRes?.data?.audioLen ?? 2;
        isBroadcast.value = true;
        avatarRenderer.value.startPlay();

        avatarRenderer.value.receiveData(atfRes.data, false, () => {
          if (_ackAborted) return;
          console.log(`[Ack] onAnimationReady -> typewriter ${phrase.length} chars over ${audioLen}s`);
          isTyping.value = false;
          startTypewriter(phrase, audioLen * 1000);
        });

        _ackStopTimer = window.setTimeout(() => {
          _ackStopTimer = null;
          if (!_ackAborted) {
            stopTypewriter(true);
            isBroadcast.value = false;
            avatarRenderer.value?.stopPlay();
            isTyping.value = true;
            bubbleText.value = "";
          }
        }, audioLen * 1000 + 300);
      }
    } catch (e) {
      console.log("[Ack] error:", e);
    }
  })();
};

const stopAcknowledgment = () => {
  _ackAborted = true;
  if (_ackStopTimer !== null) {
    window.clearTimeout(_ackStopTimer);
    _ackStopTimer = null;
  }
};

const useOpenClaw = computed(() => true);

const resolvedAgentId = computed(() => {
  if (selectedAgentId.value && agents.value.some((a) => a.id === selectedAgentId.value)) {
    return selectedAgentId.value;
  }
  return currentAvatar.value?.agentId || "";
});

const sendViaOpenClaw = async (content: string) => {
  const avatar = currentAvatar.value;
  const agentId = resolvedAgentId.value || avatar?.agentId;
  if (!agentId) return;

  chatHistory.value.push({ role: "user", content });
  isTyping.value = true;
  bubbleVisible.value = true;
  bubbleText.value = "";
  latestAgentText.value = "";

  playAcknowledgment();

  chatAbortController.value?.abort();
  const controller = new AbortController();
  chatAbortController.value = controller;

  console.log(`[Chat] sendViaOpenClaw agent=${agentId} msgCount=${chatHistory.value.length}`);
  try {
    const fullText = await openclawChat({
      messages: chatHistory.value,
      agentId,
      stream: true,
      signal: controller.signal,
      onChunk: (text: string) => {
        latestAgentText.value = text;
      },
      onDone: async (text: string) => {
        stopAcknowledgment();
        console.log(`[Chat] onDone len=${text.length} text=${text.substring(0, 100)}`);
        chatHistory.value.push({ role: "assistant", content: text });
        pushAgentMessage(text);

        if (avatarRenderer.value && avatar?.id && avatar.id !== "sumi") {
          await playTtsWithLipSync(text);
        } else {
          isTyping.value = false;
          bubbleText.value = text;
        }
      },
      onError: (err) => {
        console.error("[Chat] onError:", err);
        isTyping.value = false;
      },
    });
    void fullText;
  } catch (err: any) {
    if (err?.name !== "AbortError") {
      isTyping.value = false;
      const fallback = translate("chat.assistantReply1");
      pushAgentMessage(fallback);
      bubbleText.value = fallback;
    }
  }
};

const playTtsWithLipSync = async (text: string) => {
  stopAcknowledgment();
  const avatar = currentAvatar.value;
  if (!avatar || !text.trim()) {
    console.log("[LipSync] EXIT: no avatar or empty text");
    isTyping.value = false;
    bubbleText.value = text;
    return;
  }

  try {
    const voiceId = resolvedTtsVoiceId.value;
    const apiBase = getApiBaseUrl();
    console.log(`[TTS] POST ${apiBase}/web-api/portrait/tts/transform voiceId=${voiceId}`);
    const ttsRes = await ttsTransform({ content: text, voiceId });
    const audioBase64 = ttsRes?.data?.audioBase64 || "";
    const audioUrl = ttsRes?.data?.audioUrl || "";
    const src = audioUrl || (audioBase64 ? `data:audio/mp3;base64,${audioBase64}` : "");
    console.log(`[TTS] code=${ttsRes?.code} audioLen=${ttsRes?.data?.audioLen} hasBase64=${!!audioBase64} hasUrl=${!!audioUrl} src=${src ? "yes" : "empty"}`);

    const atfModelId = avatar.modelId || "";
    console.log(`[LipSync] check: audioBase64=${!!audioBase64} renderer=${!!avatarRenderer.value} modelId=${atfModelId || "(empty)"}`);

    if (audioBase64 && avatarRenderer.value && atfModelId) {
      const traceId = typeof crypto?.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
      console.log(`[ATF] POST ${apiBase}/web-api/portrait/atf/dt modelId=${atfModelId}`);
      try {
        const atfRes = await atfDt({
          status: "start",
          dialogueBase64: audioBase64,
          lastDialogueBase64: "",
          modelId: atfModelId,
          traceId,
        });
        console.log(`[ATF] code=${atfRes?.code} msg=${atfRes?.msg} hasData=${!!atfRes?.data}`);
        if (atfRes?.data) {
          isBroadcast.value = true;
          avatarRenderer.value.startPlay();
          const audioLen = ttsRes?.data?.audioLen ?? 3;
          console.log(`[LipSync] waiting for onAnimationReady... audioLen=${audioLen}`);
          await new Promise<void>((resolve) => {
            avatarRenderer.value!.receiveData(atfRes.data!, false, () => {
              console.log(`[LipSync] onAnimationReady -> typewriter ${text.length} chars over ${audioLen}s`);
              isTyping.value = false;
              startTypewriter(text, audioLen * 1000);
              resolve();
            });
          });
          setTimeout(() => {
            stopTypewriter(true);
            isBroadcast.value = false;
            avatarRenderer.value?.stopPlay();
          }, audioLen * 1000 + 500);
          return;
        }
      } catch (e) {
        console.warn("[ATF] error:", e);
      }
    }

    console.log("[LipSync] fallback: ATF not available, showing text with audio only");
    isTyping.value = false;
    if (src) {
      const audioLen = ttsRes?.data?.audioLen ?? 3;
      if (avatarRenderer.value) {
        isBroadcast.value = true;
        avatarRenderer.value.startPlay();
      }
      startTypewriter(text, audioLen * 1000);
      const audio = new Audio(src);
      audio.addEventListener("ended", () => {
        stopTypewriter(true);
        isBroadcast.value = false;
        avatarRenderer.value?.stopPlay();
      });
      await audio.play();
    } else {
      bubbleText.value = text;
    }
  } catch {
    console.log("[LipSync] CATCH: error, showing text");
    isTyping.value = false;
    bubbleText.value = text;
    isBroadcast.value = false;
    avatarRenderer.value?.stopPlay();
  }
};

const send = () => {
  const content = input.value.trim();
  if (!content) return;
  pushUserMessage(content);
  input.value = "";
  latestAgentText.value = "";
  bubbleVisible.value = true;
  bubbleText.value = "";

  if (useOpenClaw.value) {
    sendViaOpenClaw(content);
    return;
  }

  isTyping.value = true;
  setTimeout(async () => {
    const next = assistantReplies.value[Math.floor(Math.random() * assistantReplies.value.length)];
    pushAgentMessage(next);

    const avatarId = currentAvatar.value?.id;
    if (avatarRenderer.value && avatarId && avatarId !== "sumi") {
      await playTtsWithLipSync(next);
    } else {
      isTyping.value = false;
      bubbleText.value = next;
    }
  }, 900);
};

const blobToBase64 = (blob: Blob): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = reader.result as string;
      const base64 = result.split(",")[1] || "";
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
};

const stopRecordingAndSend = () => {
  return new Promise<void>((resolve) => {
    if (!mediaRecorder.value || mediaRecorder.value.state === "inactive") {
      resolve();
      return;
    }
    mediaRecorder.value.onstop = async () => {
      const chunks = [...recordedChunks.value];
      stopRecordingOnly();

      if (!chunks.length) {
        resolve();
        return;
      }

      const audioBlob = new Blob(chunks, { type: "audio/webm" });
      try {
        const audioBase64 = await blobToBase64(audioBlob);
        console.log("[ASR] sending audio, size:", audioBase64.length);
        const res = await asrBase64(audioBase64);
        console.log("[ASR] response:", res);
        const text = (res.data?.text || "").trim();
        console.log("[ASR] result:", text);
        if (text) {
          input.value = text;
          send();
        }
      } catch (err) {
        console.error("[ASR] error:", err);
      }
      resolve();
    };
    mediaRecorder.value.stop();
  });
};

const toggleRecording = async () => {
  if (isRecording.value) {
    isRecording.value = false;
    await stopRecordingAndSend();
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaStream.value = stream;
    recordedChunks.value = [];
    const recorder = new MediaRecorder(stream);
    mediaRecorder.value = recorder;
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) recordedChunks.value.push(event.data);
    };
    recorder.start();
    isRecording.value = true;
  } catch {
    isRecording.value = false;
  }
};
</script>

<template>
  <section class="page chat-page" :class="{ 'agent-open': showAgentPanel }">
    <header class="chat-topbar">
      <button class="chat-back-btn" @click="router.push('/avatars')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="m15 18-6-6 6-6" />
        </svg>
        {{ translate("chat.exit") }}
      </button>
      <div class="chat-agent-info">
        <div class="chat-agent-name">{{ avatarName }}</div>
        <div class="chat-agent-status"><i class="status-dot"></i>{{ translate("chat.online") }}</div>
      </div>
      <div v-if="isChatMode" class="chat-tools">
        <button class="chat-topbar-pill" :data-tip="translate('chat.tipAgent')" @click="togglePanel('agent')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
        </button>
        <button class="chat-topbar-pill" :data-tip="translate('chat.tipVoice')" :disabled="isPublicMode" @click="togglePanel('voice')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
          </svg>
        </button>
        <button class="chat-topbar-pill" :data-tip="translate('chat.tipHistory')" @click="togglePanel('history')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </button>
        <div class="chat-mode-toggle">
          <button class="mode-btn" :class="{ active: mode === 'text' }" @click="mode = 'text'">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            {{ translate("chat.modeText") }}
          </button>
          <button class="mode-btn" :class="{ active: mode === 'voice' }" @click="mode = 'voice'">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
              <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
              <line x1="12" y1="19" x2="12" y2="23" />
            </svg>
            {{ translate("chat.modeVoice") }}
          </button>
        </div>
      </div>
    </header>

    <div class="chat-character-area">
      <div class="chat-character">
        <canvas v-if="useCanvasRenderer" ref="canvasRef" class="chat-avatar-canvas" :class="{ hidden: !isChatMode }"></canvas>
        <img
          v-else-if="waitingAvatarImage"
          :src="waitingAvatarImage"
          :alt="avatarName"
          @load="handleFallbackReady"
          @error="handleFallbackReady"
          :class="{ hidden: !isChatMode }"
        />
        <div class="speech-bubble" :class="{ visible: showSpeechBubble }">
          <div class="bubble-inner">
            <div class="bubble-name">{{ avatarName }}</div>
            <div v-if="isTyping" class="bubble-dots"><span></span><span></span><span></span></div>
            <div v-else>{{ bubbleText }}</div>
          </div>
        </div>
      </div>
      <div v-if="!isChatMode" class="chat-loading-center">
        <img
          v-if="waitingAvatarImage"
          :src="waitingAvatarImage"
          :alt="avatarName"
          class="chat-loading-avatar"
          @load="handleFallbackReady"
          @error="handleFallbackReady"
        />
        <div class="chat-loading-name">{{ avatarName }}</div>
        <div class="chat-loading-status">{{ translate("chat.online") }}</div>
      </div>
      <button v-if="isChatMode && isBroadcast" class="interrupt-button-floating" @click="stopPlaying">
        <span class="interrupt-icon" aria-hidden="true"></span>
      </button>
      <div v-if="isAvatarLoading" class="avatar-loading-mask">
        <div class="avatar-loading-spinner"></div>
        <div class="avatar-loading-text">{{ translate("chat.loadingAvatar") }}</div>
      </div>
    </div>

    <footer v-if="isChatMode" class="chat-bottom">
      <div v-if="mode === 'text'" class="chat-input-area text-mode-area">
        <div class="chat-input-wrap">
          <input
            v-model="input"
            class="chat-input"
            type="text"
            :placeholder="translate('chat.inputPlaceholder')"
            @keydown.enter="send"
          />
        </div>
        <button class="chat-send-btn" :disabled="isBroadcast" @click="send">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>
      <div v-else class="voice-mode-area active">
        <div class="voice-bar-wrap" :class="{ listening: isRecording }">
          <span class="voice-label">{{ translate("chat.tapMic") }}</span>
          <div class="v-bar" style="--bar-h:8px"></div><div class="v-bar" style="--bar-h:14px"></div>
          <div class="v-bar" style="--bar-h:20px"></div><div class="v-bar" style="--bar-h:12px"></div>
          <div class="v-bar" style="--bar-h:24px"></div><div class="v-bar" style="--bar-h:16px"></div>
          <div class="v-bar" style="--bar-h:28px"></div><div class="v-bar" style="--bar-h:18px"></div>
          <div class="v-bar" style="--bar-h:22px"></div><div class="v-bar" style="--bar-h:10px"></div>
          <div class="v-bar" style="--bar-h:26px"></div><div class="v-bar" style="--bar-h:14px"></div>
          <div class="v-bar" style="--bar-h:30px"></div><div class="v-bar" style="--bar-h:16px"></div>
          <div class="v-bar" style="--bar-h:22px"></div><div class="v-bar" style="--bar-h:12px"></div>
          <div class="v-bar" style="--bar-h:26px"></div><div class="v-bar" style="--bar-h:20px"></div>
          <div class="v-bar" style="--bar-h:14px"></div><div class="v-bar" style="--bar-h:24px"></div>
          <div class="v-bar" style="--bar-h:10px"></div><div class="v-bar" style="--bar-h:18px"></div>
        </div>
        <button class="voice-mic-btn" :class="{ recording: isRecording }" @click="toggleRecording">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2" stroke-linecap="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
          </svg>
        </button>
      </div>
    </footer>

    <div v-if="isChatMode" class="overlay" :class="{ open: showHistoryPanel || showVoicePanel || showAgentPanel }" @click="closePanels" />

    <aside v-if="isChatMode" class="slide-panel history-slide" :class="{ open: showHistoryPanel }">
      <div class="panel-header history-header">
        <strong>
          {{ translate("chat.historyTitle") }}
          <span class="history-count">{{ state.chatMessages.length }}</span>
        </strong>
        <button class="panel-close" @click="showHistoryPanel = false">✕</button>
      </div>
      <div ref="historyListRef" class="history-list">
        <div v-if="!state.chatMessages.length" class="empty">{{ translate("chat.historyEmpty") }}</div>
        <div
          v-for="item in state.chatMessages"
          :key="item.id"
          class="history-item"
          :class="{ user: item.isUser }"
        >
          <div class="history-speaker">{{ item.speaker }}</div>
          <p class="history-bubble">{{ item.content }}</p>
          <div class="history-time">{{ item.time }}</div>
        </div>
      </div>
    </aside>

    <aside v-if="isChatMode" class="slide-panel voice-slide" :class="{ open: showVoicePanel }">
      <div class="panel-header">
        <strong>{{ translate("chat.voiceTitle") }}</strong>
        <button class="panel-close" @click="showVoicePanel = false">✕</button>
      </div>
      <div class="vs-body">
        <div v-for="group in voiceGroups" :key="group.group" class="vs-lang-group">
          <div class="vs-lang-label">{{ group.group }}</div>
          <div class="vs-voice-list">
            <button
              v-for="voice in group.items"
              :key="voice.id"
              class="vs-voice-item"
              :class="{ active: selectedVoice === voice.id }"
              :disabled="voiceLoading || voiceSavingId === voice.id"
              @click="applyVoiceSelection(voice)"
            >
              <div class="vs-voice-icon" :class="voice.gender">{{ voice.gender === "female" ? "♀" : "♂" }}</div>
              <div class="vs-voice-info">
                <div class="vs-voice-name">{{ voice.name }}</div>
                <div class="vs-voice-desc">{{ voice.desc }}</div>
              </div>
              <div class="vs-voice-check"></div>
            </button>
          </div>
        </div>
      </div>
    </aside>

    <aside v-if="isChatMode" class="slide-panel agent-slide" :class="{ open: showAgentPanel }">
      <div class="panel-header">
        <strong>{{ translate("chat.agentTitle") }}</strong>
        <div class="panel-header-actions">
          <button class="agent-sync-btn" :disabled="agentsLoading" @click="syncAgents">&#x21bb;</button>
          <button class="panel-close" @click="showAgentPanel = false">&#x2715;</button>
        </div>
      </div>
      <div v-if="agentsLoading && !agents.length" class="agent-loading">
        <div class="upload-loading-spinner"></div>
      </div>
      <div v-else-if="!agents.length" class="agent-empty">{{ translate("create.noAgentsFound") }}</div>
      <div v-else class="agent-list">
        <button
          v-for="agent in agents"
          :key="agent.id"
          class="agent-item"
          :class="{ active: selectedAgentId === agent.id }"
          @click="selectedAgentId = agent.id"
        >
          <div class="agent-item-info">
            <strong>{{ agent.name }}</strong>
            <p>{{ agent.desc }}</p>
          </div>
          <span class="tag">{{ agent.type }}</span>
          <button class="agent-detail-btn" @click.stop="showAgentDetail = true">&#x24D8;</button>
          <span class="agent-check" :class="{ active: selectedAgentId === agent.id }"></span>
        </button>
      </div>
      <p class="panel-tip">{{ translate("chat.agentTip") }}</p>
    </aside>

    <div v-if="isChatMode" class="detail-modal" :class="{ open: showAgentDetail }" @click="showAgentDetail = false">
      <div class="detail-card" @click.stop>
        <h3>{{ currentAgent.name }}</h3>
        <p>{{ currentAgent.type }} Agent</p>
        <p>{{ currentAgent.persona }}</p>
        <button class="primary-btn" @click="showAgentDetail = false">{{ translate("chat.close") }}</button>
      </div>
    </div>
  </section>
</template>
