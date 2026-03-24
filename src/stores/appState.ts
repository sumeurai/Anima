import { reactive } from "vue";
import { translate } from "../locales";

export type Avatar = {
  id: string;
  name: string;
  desc: string;
  type: "official" | "custom" | "public";
  image: string;
  modelUrl?: string;
  modelId?: string;
  voiceId?: string;
  generating?: boolean;
  progress?: number;
  agentId?: string;
};

export type ChatMessage = {
  id: string;
  speaker: string;
  content: string;
  isUser: boolean;
  time: string;
};

type RemoteAvatarItem = {
  avatarsId?: string | number;
  id?: string | number;
  nickname?: string;
  avatarImg?: string;
  image?: string;
  downloadLink?: string;
  modelUrl?: string;
  modelId?: string | number;
  voiceId?: string | number;
  ttsId?: string | number;
  defaultVoiceId?: string | number;
  builtinTts?: Array<{ ttsId?: string | number }>;
  tts?: Array<{ ttsId?: string | number }>;
  avatarsStatus?: string | number;
  isCustomize?: string | number;
};

const DEFAULT_AVATAR_VOICE_ID = "zh-female";
const DEFAULT_USER_EMAIL = "user@email.com";
const PUBLIC_AVATAR_IDS = ["1453322680344576", "1453328544415744"];
const PUBLIC_AVATAR_NAMES: Record<string, string> = {
  "1453322680344576": "Sumi",
  "1453328544415744": "Tomo",
};
const AGENT_MAP_KEY = "avatarAgentMap";

const readAgentMap = (): Record<string, string> => {
  try {
    return JSON.parse(localStorage.getItem(AGENT_MAP_KEY) || "{}");
  } catch {
    return {};
  }
};

const readAgentId = (avatarId: string): string | undefined => {
  return readAgentMap()[avatarId] || undefined;
};

const saveAgentId = (avatarId: string, agentId: string) => {
  const map = readAgentMap();
  map[avatarId] = agentId;
  localStorage.setItem(AGENT_MAP_KEY, JSON.stringify(map));
};

const readStoredEmail = (): string => {
  try {
    const raw = localStorage.getItem("userStore");
    if (!raw) return DEFAULT_USER_EMAIL;
    const parsed = JSON.parse(raw) as { userData?: { mailbox?: string } };
    return parsed?.userData?.mailbox || DEFAULT_USER_EMAIL;
  } catch {
    return DEFAULT_USER_EMAIL;
  }
};

const state = reactive({
  userEmail: readStoredEmail(),
  selectedAvatarId: "sumi",
  avatars: [
    {
      id: "sumi",
      name: "Sumi",
      desc: translate("store.defaultAvatarDesc"),
      type: "official",
      image: "/sumi.png",
      voiceId: DEFAULT_AVATAR_VOICE_ID,
    } as Avatar,
  ],
  chatMessages: [] as ChatMessage[],
});

const nowTime = () => {
  const now = new Date();
  const hh = now.getHours().toString().padStart(2, "0");
  const mm = now.getMinutes().toString().padStart(2, "0");
  return `${hh}:${mm}`;
};

const useAppState = () => {
  const normalizeVoiceId = (value?: string | number) => {
    const normalized = String(value ?? "").trim();
    return normalized || DEFAULT_AVATAR_VOICE_ID;
  };

  const selectedAvatar = () =>
    state.avatars.find((item) => item.id === state.selectedAvatarId) ?? state.avatars[0];

  const login = (email: string) => {
    state.userEmail = email || DEFAULT_USER_EMAIL;
  };

  const selectAvatar = (id: string) => {
    state.selectedAvatarId = id;
  };

  const createAvatarTask = (
    name: string,
    options?: { id?: string; image?: string; voiceId?: string; mockProgress?: boolean; agentId?: string },
  ) => {
    const id = options?.id || `avatar-${Date.now()}`;
    const existing = state.avatars.find((item) => item.id === id);
    const taskAvatar: Avatar = {
      id,
      name: name || existing?.name || translate("store.defaultAvatarName"),
      desc: translate("store.generatingDesc"),
      type: "custom",
      image: options?.image || existing?.image || "/sumi.png",
      voiceId: normalizeVoiceId(options?.voiceId || existing?.voiceId),
      generating: true,
      progress: 0,
      agentId: options?.agentId || existing?.agentId,
    };
    if (taskAvatar.agentId) {
      saveAgentId(id, taskAvatar.agentId);
    }
    if (existing) {
      Object.assign(existing, taskAvatar);
    } else {
      state.avatars.push(taskAvatar);
    }

    if (options?.mockProgress === false) {
      const target = state.avatars.find((item) => item.id === id);
      if (target) {
        target.progress = 0;
        target.desc = translate("store.generatingServerDesc");
      }
      return;
    }

    const timer = setInterval(() => {
      const target = state.avatars.find((item) => item.id === id);
      if (!target) {
        clearInterval(timer);
        return;
      }
      const nextProgress = Math.min((target.progress ?? 0) + Math.round(Math.random() * 6 + 2), 100);
      target.progress = nextProgress;
      if (nextProgress >= 100) {
        target.generating = false;
        target.desc = translate("store.generatedDesc");
        target.progress = 100;
        clearInterval(timer);
      }
    }, 1200);
  };

  const syncAvatarsFromServer = (remoteList: RemoteAvatarItem[]) => {
    const kept = state.avatars.filter((item) => item.type === "official" || item.type === "public");
    const mappedCustom: Avatar[] = (remoteList || [])
      .map((item) => {
        const id = String(item.avatarsId ?? item.id ?? "").trim();
        if (!id) return null;
        const existing = state.avatars.find((avatar) => avatar.id === id);
        const status = String(item.avatarsStatus ?? "");
        const modelUrl = item.downloadLink || item.modelUrl || "";
        const hasModel = Boolean(String(modelUrl).trim());
        // List API avatarsStatus: "0" = no model/sound, "1" = complete, "2" = no brain (usable).
        const isDoneByStatus = status === "1" || status === "2";
        const isFailedByStatus =
          status === "42" ||
          status === "43" ||
          status.toLowerCase() === "style-error" ||
          status.toLowerCase() === "model-error";
        const hasLocalDoneFlag = Boolean(
          existing && !existing.generating && (String(existing.modelUrl || "").trim() || (existing.progress ?? 0) >= 100),
        );
        const isDone = isDoneByStatus || hasModel || hasLocalDoneFlag;
        const isGenerating = !isDone && !isFailedByStatus;
        const voiceFromBuiltin = item.builtinTts?.find((voice) => voice?.ttsId)?.ttsId;
        const voiceFromTts = item.tts?.find((voice) => voice?.ttsId)?.ttsId;
        return {
          id,
          name: item.nickname || translate("store.defaultAvatarName"),
          desc: isGenerating
            ? translate("store.generatingServerDesc")
            : isFailedByStatus
              ? translate("store.failedDesc")
            : isDone
              ? translate("store.generatedDesc")
              : translate("store.unknownStatusDesc"),
          type: (String(item.isCustomize ?? "1") === "1" ? "custom" : "custom") as "custom",
          image: item.avatarImg || item.image || "/sumi.png",
          modelUrl: modelUrl || existing?.modelUrl || "",
          modelId: String(item.modelId ?? existing?.modelId ?? "").trim() || undefined,
          voiceId: normalizeVoiceId(
            item.voiceId ?? item.ttsId ?? item.defaultVoiceId ?? voiceFromBuiltin ?? voiceFromTts ?? existing?.voiceId,
          ),
          generating: isGenerating,
          progress: isGenerating ? 0 : isDone ? 100 : 0,
          agentId: existing?.agentId || readAgentId(id),
        } as Avatar;
      })
      .filter((item): item is Avatar => Boolean(item));

    state.avatars = [...kept, ...mappedCustom];
    const selectedExists = state.avatars.some((item) => item.id === state.selectedAvatarId);
    if (!selectedExists && state.avatars.length > 0) {
      state.selectedAvatarId = state.avatars[0].id;
    }
  };

  const completeAvatarGeneration = (id: string, image?: string) => {
    const target = state.avatars.find((item) => item.id === id);
    if (!target) return;
    target.generating = false;
    target.progress = 100;
    target.desc = translate("store.generatedDesc");
    if (image) target.image = image;
    target.voiceId = normalizeVoiceId(target.voiceId);
  };

  const failAvatarGeneration = (id: string, message?: string) => {
    const target = state.avatars.find((item) => item.id === id);
    if (!target) return;
    target.generating = false;
    target.progress = 0;
    target.desc = message || translate("store.failedDesc");
  };

  const pushUserMessage = (content: string) => {
    state.chatMessages.push({
      id: `${Date.now()}-u`,
      speaker: translate("store.defaultSpeaker"),
      content,
      isUser: true,
      time: nowTime(),
    });
  };

  const pushAgentMessage = (content: string) => {
    const avatar = selectedAvatar();
    state.chatMessages.push({
      id: `${Date.now()}-a`,
      speaker: avatar?.name ?? translate("store.defaultAgentSpeaker"),
      content,
      isUser: false,
      time: nowTime(),
    });
  };

  return {
    state,
    selectedAvatar,
    login,
    selectAvatar,
    createAvatarTask,
    syncAvatarsFromServer,
    completeAvatarGeneration,
    failAvatarGeneration,
    pushUserMessage,
    pushAgentMessage,
  };
};

export { useAppState };
export { DEFAULT_AVATAR_VOICE_ID };
export { DEFAULT_USER_EMAIL };
export { PUBLIC_AVATAR_IDS, PUBLIC_AVATAR_NAMES };
