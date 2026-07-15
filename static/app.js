(() => {
  "use strict";

  const elements = {};
  const state = {
    result: null,
    activeIndex: 0,
    requestController: null,
    expiryTimer: null,
    toastTimer: null,
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function refreshIcons() {
    if (window.lucide) {
      window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
    }
  }

  function extractFirstHttpUrl(value) {
    const input = String(value || "").trim();
    const match = input.match(/https?:\/\/[^\s<>"']+/i);
    if (match) {
      return match[0].replace(/[.,!?;:)\]}，。！？；：、）】》」』]+$/u, "");
    }
    if (!input || /\s/.test(input)) return null;
    return input.includes("://") ? input : `https://${input.replace(/^\/+/, "")}`;
  }

  function matchesDomain(host, domain) {
    return host === domain || host.endsWith(`.${domain}`);
  }

  function detectPlatform(value) {
    const url = extractFirstHttpUrl(value);
    if (!url) return null;
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.toLowerCase();
      if (matchesDomain(host, "instagram.com")) return "instagram";
      if (matchesDomain(host, "tiktok.com")) return "tiktok";
      if (matchesDomain(host, "douyin.com") || matchesDomain(host, "iesdouyin.com")) return "douyin";
      if (matchesDomain(host, "bilibili.com") || matchesDomain(host, "b23.tv")) return "bilibili";
    } catch (_) {
      return null;
    }
    return null;
  }

  function updatePlatformIndicator() {
    const platform = detectPlatform(elements.urlInput.value);
    const icon = elements.detectedPlatform.querySelector("[data-lucide]");
    const label = elements.detectedPlatform.querySelector("span");
    elements.detectedPlatform.dataset.platform = platform || "auto";
    elements.loadingPlatform.textContent = platform ? platform.toUpperCase() : "AUTO";
    if (platform === "instagram") {
      icon.setAttribute("data-lucide", "instagram");
      label.textContent = "Instagram";
    } else if (platform === "tiktok") {
      icon.setAttribute("data-lucide", "music-2");
      label.textContent = "TikTok";
    } else if (platform === "douyin") {
      icon.setAttribute("data-lucide", "music");
      label.textContent = "Douyin";
    } else if (platform === "bilibili") {
      icon.setAttribute("data-lucide", "tv");
      label.textContent = "Bilibili";
    } else {
      icon.setAttribute("data-lucide", "link-2");
      label.textContent = "自动识别";
    }
    elements.clearButton.hidden = !elements.urlInput.value;
    refreshIcons();
  }

  function setLoading(loading) {
    elements.submitButton.disabled = loading;
    elements.loadingPanel.hidden = !loading;
    const icon = elements.submitButton.querySelector("[data-lucide]");
    const label = elements.submitButton.querySelector("span");
    if (loading) {
      icon.setAttribute("data-lucide", "loader-circle");
      icon.classList.add("spin-icon");
      label.textContent = "解析中";
      elements.emptyState.hidden = true;
      elements.resultPanel.hidden = true;
    } else {
      icon.setAttribute("data-lucide", "scan-search");
      icon.classList.remove("spin-icon");
      label.textContent = "解析媒体";
    }
    refreshIcons();
  }

  function showError(message) {
    elements.formError.textContent = message;
    elements.formError.hidden = false;
    elements.urlField.setAttribute("aria-invalid", "true");
  }

  function clearError() {
    elements.formError.hidden = true;
    elements.formError.textContent = "";
    elements.urlField.removeAttribute("aria-invalid");
  }

  function showToast(message) {
    window.clearTimeout(state.toastTimer);
    elements.toastText.textContent = message;
    elements.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => {
      elements.toast.hidden = true;
    }, 2400);
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(Number(seconds))) return "—";
    const total = Math.max(0, Math.floor(Number(seconds)));
    const minutes = Math.floor(total / 60);
    const remainder = String(total % 60).padStart(2, "0");
    return `${minutes}:${remainder}`;
  }

  function formatCount(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "0";
    return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(number);
  }

  function mediaLabel(item) {
    if (item.kind === "video") return `视频 · ${(item.format || "MP4").toUpperCase()}`;
    return `图片 · ${(item.format || "IMAGE").toUpperCase()}`;
  }

  function renderMediaError() {
    const wrapper = document.createElement("div");
    wrapper.className = "media-error";
    const icon = document.createElement("i");
    icon.setAttribute("data-lucide", "circle-alert");
    const text = document.createElement("span");
    text.textContent = "媒体会话失效，请重新解析";
    wrapper.append(icon, text);
    elements.mediaStage.replaceChildren(wrapper);
    refreshIcons();
  }

  function renderActiveMedia(index) {
    const result = state.result;
    const item = result.media[index];
    if (!item) return;
    state.activeIndex = index;
    elements.mediaStage.replaceChildren();

    if (item.kind === "video") {
      const video = document.createElement("video");
      video.controls = true;
      video.playsInline = true;
      video.preload = "metadata";
      video.src = item.preview_url;
      if (result.thumbnail_url) video.poster = result.thumbnail_url;
      video.addEventListener("error", renderMediaError, { once: true });
      elements.mediaStage.append(video);
    } else {
      const image = document.createElement("img");
      image.src = item.preview_url;
      image.alt = result.caption || `${result.platform} 图片 ${index + 1}`;
      image.loading = "eager";
      image.addEventListener("error", renderMediaError, { once: true });
      elements.mediaStage.append(image);
    }

    elements.mediaCounter.textContent = `${String(index + 1).padStart(2, "0")} / ${String(result.media.length).padStart(2, "0")}`;
    elements.openButton.href = item.preview_url;
    elements.downloadButton.href = item.download_url;
    elements.mediaType.textContent = mediaLabel(item);
    elements.mediaQuality.textContent = item.quality || "原始";
    elements.mediaSize.textContent = item.width && item.height ? `${item.width} × ${item.height}` : "原始";
    elements.mediaDuration.textContent = item.kind === "video" ? formatDuration(item.duration) : "—";

    for (const [buttonIndex, button] of Array.from(elements.thumbnailRail.children).entries()) {
      button.setAttribute("aria-current", buttonIndex === index ? "true" : "false");
    }
  }

  function renderThumbnails(result) {
    elements.thumbnailRail.replaceChildren();
    elements.thumbnailRail.hidden = result.media.length <= 1;
    if (result.media.length <= 1) return;

    result.media.forEach((item, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "thumbnail-button";
      button.setAttribute("aria-label", `查看第 ${index + 1} 个媒体`);
      button.setAttribute("aria-current", index === 0 ? "true" : "false");

      const image = document.createElement("img");
      image.src = item.kind === "image" ? item.preview_url : result.thumbnail_url || item.preview_url;
      image.alt = "";
      image.loading = "lazy";
      button.append(image);

      if (item.kind === "video") {
        const badge = document.createElement("span");
        const icon = document.createElement("i");
        icon.setAttribute("data-lucide", "play");
        badge.append(icon);
        button.append(badge);
      }
      button.addEventListener("click", () => renderActiveMedia(index));
      elements.thumbnailRail.append(button);
    });
  }

  const statConfig = {
    playCount: ["play", "播放"],
    diggCount: ["heart", "点赞"],
    commentCount: ["message-circle", "评论"],
    shareCount: ["send", "分享"],
  };

  function renderStats(stats) {
    elements.statsRow.replaceChildren();
    const entries = Object.entries(stats || {}).filter(([key]) => statConfig[key]);
    elements.statsRow.hidden = entries.length === 0;
    for (const [key, value] of entries) {
      const item = document.createElement("div");
      item.className = "stat-item";
      const icon = document.createElement("i");
      icon.setAttribute("data-lucide", statConfig[key][0]);
      const label = document.createElement("span");
      label.textContent = statConfig[key][1];
      const count = document.createElement("strong");
      count.textContent = formatCount(value);
      item.append(icon, label, count);
      elements.statsRow.append(item);
    }
  }

  function startExpiry(seconds) {
    window.clearInterval(state.expiryTimer);
    let remaining = Number(seconds) || 900;
    const update = () => {
      elements.expiryValue.textContent = formatDuration(remaining);
      if (remaining <= 0) {
        window.clearInterval(state.expiryTimer);
        elements.expiryValue.textContent = "EXPIRED";
        return;
      }
      remaining -= 1;
    };
    update();
    state.expiryTimer = window.setInterval(update, 1000);
  }

  function renderResult(result) {
    state.result = result;
    state.activeIndex = 0;
    elements.emptyState.hidden = true;
    elements.resultPanel.hidden = false;
    elements.resultPlatform.dataset.platform = result.platform;
    elements.resultPlatform.textContent = result.platform.toUpperCase();
    const platformTitles = {
      instagram: "Instagram 媒体",
      tiktok: "TikTok 媒体",
      douyin: "Douyin 媒体",
      bilibili: "Bilibili 媒体",
    };
    elements.resultTitle.textContent = platformTitles[result.platform] || "解析完成";
    elements.creatorName.textContent = result.author_name || (result.author ? `@${result.author}` : "未知发布者");
    elements.captionText.textContent = result.caption || "无文字内容";
    elements.sourceButton.href = result.original_url;

    elements.creatorAvatar.replaceChildren();
    if (result.author_avatar_url) {
      const avatar = document.createElement("img");
      avatar.src = result.author_avatar_url;
      avatar.alt = "";
      elements.creatorAvatar.append(avatar);
    } else {
      const icon = document.createElement("i");
      icon.setAttribute("data-lucide", "user-round");
      elements.creatorAvatar.append(icon);
    }

    renderThumbnails(result);
    renderActiveMedia(0);
    renderStats(result.stats);
    startExpiry(result.expires_in);
    refreshIcons();

    elements.resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function parseUrl(url) {
    if (state.requestController) state.requestController.abort();
    state.requestController = new AbortController();
    const response = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
      signal: state.requestController.signal,
    });
    let payload;
    try {
      payload = await response.json();
    } catch (_) {
      throw new Error(`服务返回异常（HTTP ${response.status}）`);
    }
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || `解析失败（HTTP ${response.status}）`);
    }
    return payload;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    clearError();
    const input = elements.urlInput.value.trim();
    const url = extractFirstHttpUrl(input);
    const platform = detectPlatform(input);
    if (!input) {
      showError("请输入媒体链接或分享文案");
      elements.urlInput.focus();
      return;
    }
    if (!platform) {
      showError("请输入 Instagram、TikTok、Douyin 或 Bilibili 链接");
      elements.urlInput.focus();
      return;
    }

    setLoading(true);
    try {
      const result = await parseUrl(url);
      renderResult(result);
    } catch (error) {
      if (error.name !== "AbortError") {
        showError(error.message || "解析失败");
        elements.emptyState.hidden = false;
      }
    } finally {
      setLoading(false);
    }
  }

  async function pasteUrl() {
    clearError();
    try {
      const text = await navigator.clipboard.readText();
      if (!text) throw new Error("剪贴板为空");
      elements.urlInput.value = text.trim();
      updatePlatformIndicator();
      elements.urlInput.focus();
    } catch (error) {
      showToast(error.message || "无法读取剪贴板");
    }
  }

  async function copyActiveUrl() {
    const item = state.result?.media?.[state.activeIndex];
    if (!item) return;
    const url = new URL(item.preview_url, window.location.origin).href;
    try {
      await navigator.clipboard.writeText(url);
      showToast("媒体地址已复制");
    } catch (_) {
      showToast("复制失败");
    }
  }

  function toggleTheme() {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("media-resolver-theme", next);
  }

  function initialize() {
    const ids = [
      "parseForm", "urlInput", "urlField", "detectedPlatform", "pasteButton", "clearButton",
      "submitButton", "formError", "loadingPanel", "loadingPlatform", "emptyState", "resultPanel",
      "resultPlatform", "resultTitle", "mediaStage", "thumbnailRail", "mediaCounter", "copyButton",
      "openButton", "downloadButton", "creatorAvatar", "creatorName", "sourceButton", "captionText",
      "mediaType", "mediaQuality", "mediaSize", "mediaDuration", "statsRow", "expiryValue",
      "themeToggle", "toast", "toastText",
    ];
    for (const id of ids) elements[id] = byId(id);

    elements.parseForm.addEventListener("submit", handleSubmit);
    elements.urlInput.addEventListener("input", () => {
      clearError();
      updatePlatformIndicator();
    });
    elements.pasteButton.addEventListener("click", pasteUrl);
    elements.clearButton.addEventListener("click", () => {
      elements.urlInput.value = "";
      updatePlatformIndicator();
      clearError();
      elements.urlInput.focus();
    });
    elements.copyButton.addEventListener("click", copyActiveUrl);
    elements.themeToggle.addEventListener("click", toggleTheme);

    updatePlatformIndicator();
    refreshIcons();
  }

  document.addEventListener("DOMContentLoaded", initialize);
})();
