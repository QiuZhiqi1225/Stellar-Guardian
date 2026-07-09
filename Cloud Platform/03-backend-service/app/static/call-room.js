const sessionId = window.location.pathname.split("/").filter(Boolean).pop();

const roomElements = {
  status: document.getElementById("room-status"),
  statusDot: document.getElementById("room-status-dot"),
  sessionId: document.getElementById("room-session-id"),
  remoteCount: document.getElementById("room-remote-count"),
  backendUrl: document.getElementById("room-backend-url"),
  joinForm: document.getElementById("join-form"),
  role: document.getElementById("join-role"),
  label: document.getElementById("join-label"),
  participantId: document.getElementById("join-participant-id"),
  joinFeedback: document.getElementById("join-feedback"),
  startCallButton: document.getElementById("start-call-button"),
  muteButton: document.getElementById("mute-button"),
  endCallButton: document.getElementById("end-call-button"),
  localAudioState: document.getElementById("local-audio-state"),
  remoteAudioState: document.getElementById("remote-audio-state"),
  remoteAudio: document.getElementById("remote-audio"),
  participantsEmpty: document.getElementById("participants-empty"),
  participantsList: document.getElementById("participants-list"),
  logList: document.getElementById("log-list"),
};

const params = new URLSearchParams(window.location.search);
const shouldAutoJoin = params.get("autojoin") === "1";

const state = {
  joined: false,
  role: params.get("role") || "caregiver",
  label: params.get("label") || "",
  participantId: params.get("participant_id") || `${params.get("role") || "caregiver"}-${crypto.randomUUID()}`,
  localStream: null,
  peerConnection: null,
  joinedParticipants: [],
  lastSignalId: 0,
  pollHandle: null,
  participantsHandle: null,
  remoteParticipantId: "",
  offerCreated: false,
  muted: false,
};

function defaultIceServers() {
  return [{ urls: ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"] }];
}

let rtcConfig = {
  iceServers: defaultIceServers(),
};

function addLog(message) {
  const item = document.createElement("div");
  item.className = "log-item";
  item.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
  roomElements.logList.prepend(item);
}

async function loadRtcConfig() {
  try {
    const response = await AppApi.apiFetch("/api/webrtc-config");
    if (!response.ok) {
      throw new Error("Failed to load WebRTC config.");
    }
    const data = await response.json();
    const iceServers = Array.isArray(data.ice_servers) && data.ice_servers.length ? data.ice_servers : defaultIceServers();
    rtcConfig = { iceServers };
    if (data.source === "twilio") {
      addLog("已加载 Twilio 动态 TURN/STUN 配置。");
    } else if (data.source === "static_fallback" && data.warning) {
      addLog(`Twilio TURN 获取失败，已回退到静态 ICE 配置：${data.warning}`);
    } else {
      addLog(data.has_turn ? "已加载 TURN/STUN 公网穿透配置。" : "已加载 STUN 配置；跨公网建议再补 TURN。");
    }
  } catch (error) {
    rtcConfig = { iceServers: defaultIceServers() };
    addLog(`加载 WebRTC 配置失败，已退回默认 STUN：${error}`);
  }
}

function setRoomStatus(text, color = "var(--accent)") {
  roomElements.status.textContent = text;
  roomElements.statusDot.style.background = color;
}

function applyInitialIdentity() {
  roomElements.sessionId.textContent = sessionId || "-";
  roomElements.role.value = state.role;
  roomElements.label.value = state.label;
  roomElements.participantId.value = state.participantId;
  roomElements.backendUrl.textContent = AppApi.getBaseUrl();
}

function syncControls() {
  roomElements.startCallButton.disabled = !state.joined || !state.remoteParticipantId;
  roomElements.muteButton.disabled = !state.localStream;
  roomElements.endCallButton.disabled = !state.joined && !state.peerConnection && !state.localStream;
}

function clearPolling() {
  if (state.pollHandle) {
    clearInterval(state.pollHandle);
    state.pollHandle = null;
  }
  if (state.participantsHandle) {
    clearInterval(state.participantsHandle);
    state.participantsHandle = null;
  }
}

function stopLocalAudio() {
  if (state.localStream) {
    state.localStream.getTracks().forEach((track) => {
      track.stop();
    });
  }
  state.localStream = null;
  state.muted = false;
  roomElements.localAudioState.textContent = "本地麦克风未启动";
  roomElements.muteButton.textContent = "静音麦克风";
}

function resetRoomState() {
  state.offerCreated = false;
  state.remoteParticipantId = "";
  roomElements.remoteCount.textContent = "0";
  roomElements.remoteAudio.srcObject = null;
  roomElements.remoteAudioState.textContent = "远端音频未连接";
  syncControls();
}

async function ensureLocalAudio() {
  if (state.localStream) return state.localStream;
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  state.localStream = stream;
  roomElements.localAudioState.textContent = "本地麦克风已启动";
  addLog("已获取本地麦克风权限。");
  syncControls();
  return stream;
}

function createPeerConnection() {
  if (state.peerConnection) return state.peerConnection;
  const connection = new RTCPeerConnection(rtcConfig);

  connection.onicecandidate = (event) => {
    if (!event.candidate) return;
    postSignal("ice-candidate", { candidate: event.candidate.toJSON() }, state.remoteParticipantId || null).catch(
      () => {
        addLog("发送 ICE candidate 失败。");
      }
    );
  };

  connection.ontrack = (event) => {
    const [remoteStream] = event.streams;
    if (remoteStream) {
      roomElements.remoteAudio.srcObject = remoteStream;
      roomElements.remoteAudioState.textContent = "远端音频已连接";
      setRoomStatus("通话中", "var(--ok)");
      addLog("已收到远端音频流。");
    }
  };

  connection.onconnectionstatechange = () => {
    const current = connection.connectionState || "new";
    addLog(`WebRTC 连接状态：${current}`);
    if (current === "connected") {
      setRoomStatus("已连接", "var(--ok)");
    } else if (current === "failed" || current === "disconnected") {
      setRoomStatus("连接异常", "var(--accent)");
    }
  };

  state.peerConnection = connection;
  return connection;
}

async function attachLocalTracks() {
  const stream = await ensureLocalAudio();
  const connection = createPeerConnection();
  const senders = connection.getSenders();
  stream.getTracks().forEach((track) => {
    const exists = senders.some((sender) => sender.track && sender.track.id === track.id);
    if (!exists) {
      connection.addTrack(track, stream);
    }
  });
}

async function joinRoom(event) {
  if (event) event.preventDefault();
  if (state.joined) {
    roomElements.joinFeedback.textContent = "当前设备已经在房间内，无需重复加入。";
    syncControls();
    return;
  }
  const payload = {
    participant_id: roomElements.participantId.value.trim(),
    role: roomElements.role.value,
    label: roomElements.label.value.trim() || roomElements.role.value,
  };

  const response = await AppApi.apiFetch(`/api/call-sessions/${encodeURIComponent(sessionId)}/join`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error("Failed to join session.");
  }

  state.role = payload.role;
  state.label = payload.label;
  state.participantId = payload.participant_id;
  state.joined = true;
  applyInitialIdentity();
  resetRoomState();
  roomElements.joinFeedback.textContent = "已加入房间，等待另一端接入。";
  setRoomStatus("已加入房间", "var(--ok)");
  addLog(`已加入房间，角色：${state.role}，名称：${state.label}`);

  await ensureLocalAudio();
  await attachLocalTracks();
  await refreshParticipants();

  if (!state.pollHandle) {
    state.pollHandle = setInterval(() => {
      pollSignals().catch(() => {});
    }, 1000);
  }
  if (!state.participantsHandle) {
    state.participantsHandle = setInterval(() => {
      refreshParticipants().catch(() => {});
    }, 1500);
  }

  await postSignal("ready", { ready: true });
  syncControls();
}

async function postSignal(signalType, payload = {}, targetParticipantId = null) {
  const response = await AppApi.apiFetch(`/api/call-sessions/${encodeURIComponent(sessionId)}/signals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sender_participant_id: state.participantId,
      sender_role: state.role,
      signal_type: signalType,
      payload,
      target_participant_id: targetParticipantId,
    }),
  });
  if (!response.ok) {
    throw new Error("Failed to post signal.");
  }
}

async function refreshParticipants() {
  const response = await AppApi.apiFetch(`/api/call-sessions/${encodeURIComponent(sessionId)}/participants`);
  if (!response.ok) {
    throw new Error("Failed to load participants.");
  }
  const data = await response.json();
  const items = data.items || [];
  state.joinedParticipants = items;
  const remoteParticipants = items.filter((item) => item.participant_id !== state.participantId);
  state.remoteParticipantId = remoteParticipants[0]?.participant_id || "";
  roomElements.remoteCount.textContent = String(remoteParticipants.length);

  roomElements.participantsList.innerHTML = "";
  roomElements.participantsEmpty.style.display = items.length ? "none" : "block";
  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "attempt-item";
    card.innerHTML = `
      <div class="attempt-head">
        <strong>${item.label}</strong>
        <span class="${item.participant_id === state.participantId ? "attempt-status-ok" : "attempt-status-pending"}">${item.role}</span>
      </div>
      <p class="attempt-detail">participant_id: ${item.participant_id}</p>
    `;
    roomElements.participantsList.appendChild(card);
  });

  syncControls();
  if (state.joined && state.role === "caregiver" && state.remoteParticipantId && !state.offerCreated) {
    await startCall();
  }
}

async function startCall() {
  if (!state.joined) {
    roomElements.joinFeedback.textContent = "请先加入房间。";
    syncControls();
    return;
  }
  if (!state.remoteParticipantId) {
    roomElements.joinFeedback.textContent = "还没有远端参与者加入，请先让另一台电脑进入同一会话。";
    syncControls();
    return;
  }

  await attachLocalTracks();
  const connection = createPeerConnection();
  const offer = await connection.createOffer({ offerToReceiveAudio: true });
  await connection.setLocalDescription(offer);
  await postSignal("offer", { sdp: offer.sdp, type: offer.type }, state.remoteParticipantId);
  state.offerCreated = true;
  setRoomStatus("正在呼叫对方", "var(--accent)");
  addLog(`已发送 offer 给 ${state.remoteParticipantId}。`);
  syncControls();
}

async function handleOffer(signal) {
  state.remoteParticipantId = signal.sender_participant_id;
  await attachLocalTracks();
  const connection = createPeerConnection();
  await connection.setRemoteDescription(new RTCSessionDescription(signal.payload));
  const answer = await connection.createAnswer();
  await connection.setLocalDescription(answer);
  await postSignal("answer", { sdp: answer.sdp, type: answer.type }, signal.sender_participant_id);
  setRoomStatus("已应答，等待连接", "var(--accent)");
  addLog(`已处理来自 ${signal.sender_participant_id} 的 offer。`);
  syncControls();
}

async function handleAnswer(signal) {
  const connection = createPeerConnection();
  if (connection.currentRemoteDescription) return;
  await connection.setRemoteDescription(new RTCSessionDescription(signal.payload));
  setRoomStatus("对方已接听", "var(--ok)");
  addLog(`已收到来自 ${signal.sender_participant_id} 的 answer。`);
  syncControls();
}

async function handleIceCandidate(signal) {
  const connection = createPeerConnection();
  if (!signal.payload?.candidate) return;
  try {
    await connection.addIceCandidate(new RTCIceCandidate(signal.payload.candidate));
  } catch (error) {
    addLog(`添加 ICE candidate 失败：${error}`);
  }
}

async function handleHangup(signal) {
  addLog(`收到 ${signal.sender_participant_id} 的挂断信号。`);
  setRoomStatus("对方已挂断", "var(--accent)");
  closePeerConnection();
  stopLocalAudio();
  roomElements.joinFeedback.textContent = "对方已结束通话。你可以等待新呼叫，或重新点击“加入语音房间”后再次测试。";
  if (state.joined) {
    await refreshParticipants().catch(() => {});
  }
  syncControls();
}

async function pollSignals() {
  if (!state.joined) return;
  const url = new URL(AppApi.buildUrl(`/api/call-sessions/${encodeURIComponent(sessionId)}/signals`));
  url.searchParams.set("participant_id", state.participantId);
  url.searchParams.set("since_id", String(state.lastSignalId));

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Failed to poll signals.");
  }

  const data = await response.json();
  for (const signal of data.items || []) {
    state.lastSignalId = Math.max(state.lastSignalId, signal.id);
    if (signal.signal_type === "ready") {
      addLog(`${signal.sender_role} ${signal.sender_participant_id} 已就绪。`);
      if (state.role === "caregiver" && !state.offerCreated) {
        await refreshParticipants();
      }
      continue;
    }
    if (signal.signal_type === "offer") {
      await handleOffer(signal);
      continue;
    }
    if (signal.signal_type === "answer") {
      await handleAnswer(signal);
      continue;
    }
    if (signal.signal_type === "ice-candidate") {
      await handleIceCandidate(signal);
      continue;
    }
    if (signal.signal_type === "hangup") {
      await handleHangup(signal);
    }
  }
}

function closePeerConnection() {
  if (state.peerConnection) {
    state.peerConnection.onicecandidate = null;
    state.peerConnection.ontrack = null;
    state.peerConnection.close();
    state.peerConnection = null;
  }
  resetRoomState();
}

async function leaveSessionOnServer() {
  if (!state.participantId) return;
  const response = await AppApi.apiFetch(`/api/call-sessions/${encodeURIComponent(sessionId)}/leave`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ participant_id: state.participantId }),
  });
  if (!response.ok && response.status !== 404) {
    throw new Error("Failed to leave session.");
  }
}

async function endCall() {
  clearPolling();
  if (state.joined) {
    await postSignal("hangup", { ended: true }, state.remoteParticipantId || null).catch(() => {});
    await AppApi.apiFetch(`/api/call-sessions/${encodeURIComponent(sessionId)}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "ended" }),
    }).catch(() => {});
    await leaveSessionOnServer().catch(() => {});
  }
  state.joined = false;
  closePeerConnection();
  stopLocalAudio();
  state.joinedParticipants = [];
  roomElements.participantsList.innerHTML = "";
  roomElements.participantsEmpty.style.display = "block";
  roomElements.joinFeedback.textContent = "当前设备已结束通话并退出房间。需要再次测试时，直接点“加入语音房间”即可。";
  setRoomStatus("通话已结束", "var(--accent)");
  addLog("当前端已结束通话。");
  syncControls();
}

function toggleMute() {
  if (!state.localStream) return;
  state.muted = !state.muted;
  state.localStream.getAudioTracks().forEach((track) => {
    track.enabled = !state.muted;
  });
  roomElements.muteButton.textContent = state.muted ? "取消静音" : "静音麦克风";
  addLog(state.muted ? "已静音本地麦克风。" : "已恢复本地麦克风。");
  syncControls();
}

roomElements.joinForm.addEventListener("submit", (event) => {
  joinRoom(event).catch((error) => {
    roomElements.joinFeedback.textContent = "加入房间失败，请确认共享后端地址和会话 ID 正确。";
    addLog(`加入房间失败：${error}`);
  });
});

roomElements.startCallButton.addEventListener("click", () => {
  startCall().catch((error) => {
    addLog(`发起通话失败：${error}`);
  });
});

roomElements.muteButton.addEventListener("click", () => {
  toggleMute();
});

roomElements.endCallButton.addEventListener("click", () => {
  endCall().catch((error) => {
    addLog(`结束通话失败：${error}`);
  });
});

applyInitialIdentity();
setRoomStatus("准备中");
addLog(`当前共享后端：${AppApi.getBaseUrl()}`);
syncControls();

loadRtcConfig().finally(() => {
  if (shouldAutoJoin) {
    setTimeout(() => {
      joinRoom().catch((error) => {
        roomElements.joinFeedback.textContent = "自动加入房间失败，请手动点击“加入语音房间”。";
        addLog(`自动加入失败：${error}`);
      });
    }, 150);
  }
});
