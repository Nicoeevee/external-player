from pathlib import Path

path = Path("external-player.user.js")
source = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global source
    if old not in source:
        raise SystemExit(f"Patch target not found: {label}")
    source = source.replace(old, new, 1)


def replace_between(start_marker: str, end_marker: str, replacement: str, label: str) -> None:
    global source
    start = source.find(start_marker)
    end = source.find(end_marker, start)
    if start < 0 or end < 0:
        raise SystemExit(f"Patch target not found: {label}")
    source = source[:start] + replacement + source[end:]


replace_once(
    "var iframe;\n",
    "var iframe;\n\n// Keep validation requests outside the REQUEST parser hook to avoid recursive interception.\nconst baseFetch = window.fetch.bind(window);\n",
    "base fetch",
)

replace_once(
    """    async check(video) {
        if (!video) {
            video = currentMedia.video;
        }
        if (!video || !video.startsWith('http') || video.startsWith('https://www.mp4')) {
            return false;
        }

        if (video.indexOf('.m3u8') > -1 || video.indexOf('.m3u') > -1) {
            try {
                const response = await (await fetch(video, {
                    method: 'GET',
                    credentials: 'include'
                })).body();
                return response && response.indexOf('png') === -1;
            } catch (error) {}
        }

        return new RegExp(VIDEO_URL_REGEX_EXACT).test(video);
    }
""",
    """    async check(video) {
        if (!video) {
            video = currentMedia.video;
        }
        if (!video || !video.startsWith('http') || video.startsWith('https://www.mp4')) {
            return false;
        }

        if (/\\.m3u8?(?:[?#]|$)/i.test(video)) {
            try {
                const response = await baseFetch(video, {
                    method: 'GET',
                    credentials: 'include'
                });
                const body = await response.text();
                return response.ok && body && body.indexOf('png') === -1;
            } catch (error) {
                console.debug('m3u playlist validation failed', error);
            }
        }

        return new RegExp(VIDEO_URL_REGEX_EXACT).test(video);
    }
""",
    "BaseParser.check",
)

replace_once(
    """                } catch (error) {
                    latestError = error;
                    console.error(`第${currentTryCount}次尝试解析失败：`, error);
                }
""",
    """                } catch (error) {
                    if (error instanceof VideoSelectionCancelledError) {
                        return;
                    }
                    latestError = error;
                    console.error(`第${currentTryCount}次尝试解析失败：`, error);
                }
""",
    "cancel retry handling",
)

helpers = r'''class VideoSelectionCancelledError extends Error {
    constructor() {
        super('video source selection cancelled');
        this.name = 'VideoSelectionCancelledError';
    }
}

function uniqueVideoUrls(urls) {
    return [...new Set((urls || []).filter(url => typeof url === 'string' && url.startsWith('http')))];
}

async function filterValidVideoUrls(urls, check) {
    const validUrls = [];
    for (const url of uniqueVideoUrls(urls)) {
        try {
            if (await check(url)) {
                validUrls.push(url);
            }
        } catch (error) {
            console.debug('video URL validation failed', url, error);
        }
    }
    return validUrls;
}

function isVideoVisible(video) {
    if (!video || !video.isConnected) return false;
    const style = window.getComputedStyle(video);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const rect = video.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 &&
        rect.top < window.innerHeight && rect.bottom > 0 &&
        rect.left < window.innerWidth && rect.right > 0;
}

function scoreVideoSource(url) {
    let score = 35;
    const reasons = [];
    const zh = currentConfig.global.language === 'zh';
    const videos = Array.from(document.querySelectorAll('video'));
    const matchingVideos = videos.filter(video => video.currentSrc === url || video.src === url);

    if (/\.m3u8?(?:[?#]|$)/i.test(url)) {
        score += 5;
        reasons.push(zh ? 'HLS 播放列表' : 'HLS playlist');
    }

    if (matchingVideos.length > 0) {
        score += 15;
        reasons.push(zh ? '匹配页面视频元素' : 'matches a page video element');

        const video = matchingVideos.reduce((best, item) => {
            const bestRect = best.getBoundingClientRect();
            const itemRect = item.getBoundingClientRect();
            return itemRect.width * itemRect.height > bestRect.width * bestRect.height ? item : best;
        });

        if (!video.paused && !video.ended) {
            score += 25;
            reasons.push(zh ? '正在播放' : 'currently playing');
        }
        if (isVideoVisible(video)) {
            score += 10;
            reasons.push(zh ? '当前可见' : 'currently visible');
        }
        if (video.readyState >= 2) {
            score += 5;
            reasons.push(zh ? '已加载媒体数据' : 'media data loaded');
        }
        if (video.currentTime > 0) {
            score += 5;
            reasons.push(zh ? '已有播放进度' : 'has playback progress');
        }
        if (video.ended) {
            score -= 20;
            reasons.push(zh ? '已播放结束' : 'playback ended');
        }
    }

    return {
        url,
        confidence: Math.max(0, Math.min(100, score)),
        reasons
    };
}

function buildVideoSourceCandidates(urls) {
    return uniqueVideoUrls(urls)
        .map(scoreVideoSource)
        .sort((a, b) => b.confidence - a.confidence);
}

async function selectVideoSource(urls) {
    const candidates = buildVideoSourceCandidates(urls);
    if (candidates.length === 0) return undefined;
    // Never auto-select, including when there is only one candidate.
    return showLinkSelectionModal(candidates);
}

'''
replace_once("const PARSER = {\n", helpers + "const PARSER = {\n", "selection helpers")

replace_once(
    """        async parseVideo() {
            let matchedUrls = [];
            for (const video of document.getElementsByTagName('video')) {
                if (await this.check(video.src)) {
                    matchedUrls.push(video.src);
                }
            }
            matchedUrls = [...new Set(matchedUrls)];
            if (matchedUrls.length === 1) {
                currentMedia.video = matchedUrls[0];
            } else if (matchedUrls.length > 1) {
                currentMedia.video = await showLinkSelectionModal(matchedUrls);
            }
        }
""",
    """        async parseVideo() {
            const urls = Array.from(document.getElementsByTagName('video'))
                .flatMap(video => [video.currentSrc, video.src]);
            const matchedUrls = await filterValidVideoUrls(urls, url => this.check(url));
            currentMedia.video = await selectVideoSource(matchedUrls);
        }
""",
    "VIDEO.parseVideo",
)

replace_once(
    """        async parseVideo() {
            let matchedUrls = [];
            let urls = currentUrl.match(VIDEO_URL_REGEX_GLOBAL) || [];
            for (const url of urls) {
                if (await this.check(url)) {
                    matchedUrls.push(url);
                }
            }

            for (const iframe of document.getElementsByTagName('iframe')) {
                let urls = iframe.src.match(VIDEO_URL_REGEX_GLOBAL) || [];
                for (const url of urls) {
                    if (await this.check(url)) {
                        matchedUrls.push(url);
                    }
                }
            }
            matchedUrls = [...new Set(matchedUrls)];
            if (matchedUrls.length === 1) {
                currentMedia.video = matchedUrls[0];
            } else if (matchedUrls.length > 1) {
                currentMedia.video = await showLinkSelectionModal(matchedUrls);
            }
        }
""",
    """        async parseVideo() {
            let urls = currentUrl.match(VIDEO_URL_REGEX_GLOBAL) || [];
            for (const iframe of document.getElementsByTagName('iframe')) {
                urls.push(...(iframe.src.match(VIDEO_URL_REGEX_GLOBAL) || []));
            }
            const matchedUrls = await filterValidVideoUrls(urls, url => this.check(url));
            currentMedia.video = await selectVideoSource(matchedUrls);
        }
""",
    "URL.parseVideo",
)

replace_once(
    """        async parseVideo() {
            let matchedUrls = [];
            let urls = document.body.innerHTML.match(VIDEO_URL_REGEX_GLOBAL) || [];
            for (const url of urls) {
                if (await this.check(url)) {
                    matchedUrls.push(url);
                }
            }
            matchedUrls = [...new Set(matchedUrls)];
            if (matchedUrls.length === 1) {
                currentMedia.video = matchedUrls[0];
            } else if (matchedUrls.length > 1) {
                currentMedia.video = await showLinkSelectionModal(matchedUrls);
            }
        }
""",
    """        async parseVideo() {
            const urls = document.body.innerHTML.match(VIDEO_URL_REGEX_GLOBAL) || [];
            const matchedUrls = await filterValidVideoUrls(urls, url => this.check(url));
            currentMedia.video = await selectVideoSource(matchedUrls);
        }
""",
    "HTML.parseVideo",
)

replace_once(
    """        async parseVideo() {
            let matchedUrls = [];
            for (const script of document.scripts) {
                let urls = script.innerHTML.match(VIDEO_URL_REGEX_GLOBAL) || [];
                for (const url of urls) {
                    if (await this.check(url)) {
                        matchedUrls.push(url);
                    }
                }
            }
            matchedUrls = [...new Set(matchedUrls)];
            if (matchedUrls.length === 1) {
                currentMedia.video = matchedUrls[0];
            } else if (matchedUrls.length > 1) {
                currentMedia.video = await showLinkSelectionModal(matchedUrls);
            }
        }
""",
    """        async parseVideo() {
            let urls = [];
            for (const script of document.scripts) {
                urls.push(...(script.innerHTML.match(VIDEO_URL_REGEX_GLOBAL) || []));
            }
            const matchedUrls = await filterValidVideoUrls(urls, url => this.check(url));
            currentMedia.video = await selectVideoSource(matchedUrls);
        }
""",
    "SCRIPT.parseVideo",
)

request_start = "    REQUEST: class Parser extends BaseParser {\n"
request_end = "    BILIBILI: class Parser extends BaseParser {\n"
request_replacement = r'''    REQUEST: class Parser extends BaseParser {
        constructor() {
            super();
            this.video = undefined;
            this.videos = new Map();
            this.pendingChecks = new Map();
            let that = this;

            const rememberCandidate = vurl => {
                if (!vurl) return;
                if (that.videos.has(vurl)) {
                    that.videos.set(vurl, Date.now());
                    return;
                }
                if (that.pendingChecks.has(vurl)) return;

                const pending = that.check(vurl)
                    .then(result => {
                        if (result === true) {
                            that.video = vurl;
                            that.videos.set(vurl, Date.now());
                            while (that.videos.size > 100) {
                                that.videos.delete(that.videos.keys().next().value);
                            }
                        }
                    })
                    .catch(error => console.debug('request video validation failed', error))
                    .finally(() => that.pendingChecks.delete(vurl));
                that.pendingChecks.set(vurl, pending);
            };

            const open = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function (method, url, async, user, password) {
                const urls = (typeof url === 'string' ? url.match(VIDEO_URL_REGEX_GLOBAL) : null) || [];
                urls.forEach(rememberCandidate);
                return open.apply(this, arguments);
            };

            const originalFetch = window.fetch;
            window.fetch = function (url, options) {
                return originalFetch(url, options).then(response => {
                    const urlStr = typeof url === 'string' ? url : (url && url.url ? url.url : '');
                    const urls = urlStr.match(VIDEO_URL_REGEX_GLOBAL) || [];
                    urls.forEach(rememberCandidate);
                    return response;
                });
            };
        }
        async execute() {
            await this.parseTitle();
            await this.parseVideo();
            await this.parseReferer();
            await this.parseTime();
        }
        async parseVideo() {
            await Promise.allSettled([...this.pendingChecks.values()]);

            const now = Date.now();
            const maxAge = 2 * 60 * 1000;
            for (const [url, seenAt] of this.videos) {
                if (now - seenAt > maxAge) {
                    this.videos.delete(url);
                }
            }

            const urls = [...this.videos.keys()];
            if (urls.length === 0 && this.video) {
                urls.push(this.video);
            }
            currentMedia.video = await selectVideoSource(urls);
        }
    },
'''
replace_between(request_start, request_end, request_replacement, "REQUEST parser")

modal = r'''function showLinkSelectionModal(candidates) {
    return new Promise((resolve, reject) => {
        const MODAL_DIV_ID = `${PROJECT_NAME}-modal-div`;
        let modalDiv = document.getElementById(MODAL_DIV_ID);
        if (modalDiv) {
            modalDiv.remove();
        }

        modalDiv = document.createElement('div');
        modalDiv.id = MODAL_DIV_ID;
        modalDiv.setAttribute('role', 'dialog');
        modalDiv.setAttribute('aria-modal', 'true');
        modalDiv.style.position = 'fixed';
        modalDiv.style.inset = '0';
        modalDiv.style.width = '100vw';
        modalDiv.style.height = '100vh';
        modalDiv.style.backgroundColor = 'rgba(0, 0, 0, 0.55)';
        modalDiv.style.zIndex = FIRST_Z_INDEX;
        modalDiv.style.display = 'flex';
        modalDiv.style.justifyContent = 'center';
        modalDiv.style.alignItems = 'center';

        const contentDiv = document.createElement('div');
        contentDiv.style.backgroundColor = '#fff';
        contentDiv.style.padding = '20px';
        contentDiv.style.borderRadius = '10px';
        contentDiv.style.width = 'min(760px, 88vw)';
        contentDiv.style.maxHeight = '82vh';
        contentDiv.style.overflow = 'auto';
        contentDiv.style.boxShadow = '0 8px 28px rgba(0, 0, 0, 0.24)';

        const title = document.createElement('h3');
        title.textContent = currentConfig.global.language === 'zh' ? '选择视频源' : 'Select Video Source';
        title.style.margin = '0';
        title.style.color = COLOR.TEXT;
        contentDiv.appendChild(title);

        const description = document.createElement('p');
        description.textContent = currentConfig.global.language === 'zh'
            ? '不会自动选择。置信度根据页面播放状态、可见性和媒体加载状态估算。'
            : 'No source is selected automatically. Confidence is estimated from playback, visibility, and media loading state.';
        description.style.margin = '8px 0 14px';
        description.style.fontSize = '13px';
        description.style.color = COLOR.TEXT;
        description.style.opacity = '0.8';
        contentDiv.appendChild(description);

        const list = document.createElement('div');
        list.style.display = 'flex';
        list.style.flexDirection = 'column';
        list.style.gap = '10px';

        candidates.forEach((candidate, index) => {
            const btn = document.createElement('button');
            btn.style.padding = '12px';
            btn.style.border = `1px solid ${COLOR.BORDER}`;
            btn.style.borderRadius = '7px';
            btn.style.backgroundColor = 'transparent';
            btn.style.color = COLOR.TEXT;
            btn.style.cursor = 'pointer';
            btn.style.textAlign = 'left';
            btn.style.width = '100%';

            const header = document.createElement('div');
            header.style.display = 'flex';
            header.style.justifyContent = 'space-between';
            header.style.alignItems = 'center';
            header.style.gap = '12px';

            const sourceLabel = document.createElement('strong');
            sourceLabel.textContent = `${currentConfig.global.language === 'zh' ? '视频源' : 'Source'} ${index + 1}`;
            sourceLabel.style.color = COLOR.PRIMARY;

            const confidence = document.createElement('span');
            confidence.textContent = `${currentConfig.global.language === 'zh' ? '置信度' : 'Confidence'} ${candidate.confidence}%`;
            confidence.style.whiteSpace = 'nowrap';
            confidence.style.fontWeight = 'bold';

            header.appendChild(sourceLabel);
            header.appendChild(confidence);
            btn.appendChild(header);

            const urlDiv = document.createElement('div');
            urlDiv.textContent = candidate.url;
            urlDiv.style.marginTop = '7px';
            urlDiv.style.wordBreak = 'break-all';
            urlDiv.style.fontFamily = 'monospace';
            urlDiv.style.fontSize = '12px';
            btn.appendChild(urlDiv);

            if (candidate.reasons.length > 0) {
                const reasons = document.createElement('div');
                reasons.textContent = candidate.reasons.join(' · ');
                reasons.style.marginTop = '6px';
                reasons.style.fontSize = '12px';
                reasons.style.opacity = '0.72';
                btn.appendChild(reasons);
            }

            btn.addEventListener('mouseover', () => {
                btn.style.borderColor = COLOR.PRIMARY;
            });
            btn.addEventListener('mouseout', () => {
                btn.style.borderColor = COLOR.BORDER;
            });
            btn.addEventListener('click', () => {
                cleanup();
                resolve(candidate.url);
            });
            list.appendChild(btn);
        });
        contentDiv.appendChild(list);

        const closeBtn = document.createElement('button');
        closeBtn.textContent = currentConfig.global.language === 'zh' ? '取消' : 'Cancel';
        closeBtn.style.marginTop = '16px';
        closeBtn.style.padding = '9px 18px';
        closeBtn.style.border = 'none';
        closeBtn.style.borderRadius = '5px';
        closeBtn.style.backgroundColor = COLOR.WARNING;
        closeBtn.style.color = '#fff';
        closeBtn.style.cursor = 'pointer';
        closeBtn.style.float = 'right';

        const cleanup = () => {
            document.removeEventListener('keydown', onKeydown);
            modalDiv.remove();
        };
        const cancel = () => {
            cleanup();
            reject(new VideoSelectionCancelledError());
        };
        const onKeydown = event => {
            if (event.key === 'Escape') {
                event.preventDefault();
                cancel();
            }
        };

        closeBtn.addEventListener('click', cancel);
        modalDiv.addEventListener('click', event => {
            if (event.target === modalDiv) cancel();
        });
        document.addEventListener('keydown', onKeydown);
        contentDiv.appendChild(closeBtn);

        modalDiv.appendChild(contentDiv);
        document.body.appendChild(modalDiv);
        closeBtn.focus();
    });
}

'''
replace_between(
    "function showLinkSelectionModal(",
    "function appendButtonDiv() {\n",
    modal,
    "source selection modal",
)

path.write_text(source, encoding="utf-8")
