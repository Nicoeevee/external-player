from pathlib import Path
import re

PATH = Path("external-player.user.js")
source = PATH.read_text(encoding="utf-8")

if "VIDEO_SOURCE_BASE_CONFIDENCE" in source:
    raise SystemExit("video source confidence UX is already applied")


def replace_once(old: str, new: str, label: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    source = source.replace(old, new, 1)


marker = "const MAX_TRY_COUNT = 5;\n\n"
helpers = r'''const REQUEST_CANDIDATE_TTL = 120000;

const VIDEO_SOURCE_BASE_CONFIDENCE = {
    VIDEO: 55,
    URL: 50,
    IFRAME: 48,
    HTML: 36,
    SCRIPT: 42,
    XHR: 65,
    FETCH: 65,
    REQUEST: 65,
};

class VideoSelectionCancelledError extends Error {
    constructor() {
        super('Video source selection cancelled');
        this.name = 'VideoSelectionCancelledError';
    }
}

function normalizeVideoUrl(url) {
    if (typeof url !== 'string') {
        return undefined;
    }
    const normalized = url.trim();
    return /^https?:\/\//i.test(normalized) ? normalized : undefined;
}

function extractVideoUrls(value) {
    let text = '';
    if (typeof value === 'string') {
        text = value;
    } else if (value instanceof URL) {
        text = value.href;
    } else if (value && typeof value.url === 'string') {
        text = value.url;
    }
    return [...new Set((text.match(VIDEO_URL_REGEX_GLOBAL) || [])
        .map(normalizeVideoUrl)
        .filter(Boolean))];
}

function getVideoElementUrls(video) {
    if (!video) {
        return [];
    }
    const urls = [video.currentSrc, video.src];
    try {
        for (const source of video.querySelectorAll('source[src]')) {
            urls.push(source.src);
        }
    } catch (error) {}
    return [...new Set(urls.map(normalizeVideoUrl).filter(Boolean))];
}

function isVideoElementVisible(video) {
    if (!video) {
        return false;
    }
    try {
        const style = window.getComputedStyle(video);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
            return false;
        }
        const rect = video.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 &&
            rect.top < window.innerHeight && rect.bottom > 0 &&
            rect.left < window.innerWidth && rect.right > 0;
    } catch (error) {
        return false;
    }
}

function createVideoCandidate(url, sourceName, video) {
    const normalized = normalizeVideoUrl(url);
    if (!normalized) {
        return undefined;
    }

    const sources = [...new Set((Array.isArray(sourceName) ? sourceName : [sourceName]).filter(Boolean))];
    let confidence = Math.max(30, ...sources.map(item => VIDEO_SOURCE_BASE_CONFIDENCE[item] || 30));
    const reasons = [];

    if (sources.some(item => item === 'XHR' || item === 'FETCH' || item === 'REQUEST')) {
        confidence += 6;
        reasons.push('network');
    }
    if (/\.m3u8?(?:$|[?#])/i.test(normalized)) {
        confidence += 10;
        reasons.push('hls');
    } else if (/\.(mp4|mkv|flv|m4s|mov|avi|wmv|webm)(?:$|[?#])/i.test(normalized)) {
        confidence += 8;
        reasons.push('direct');
    }

    if (video) {
        if (!video.paused && !video.ended) {
            confidence += 20;
            reasons.push('playing');
        }
        if (Number.isFinite(video.currentTime) && video.currentTime > 0) {
            confidence += 8;
            reasons.push('progress');
        }
        if (isVideoElementVisible(video)) {
            confidence += 8;
            reasons.push('visible');
        }
        if (video.readyState >= 2) {
            confidence += 5;
            reasons.push('ready');
        }
        try {
            const rect = video.getBoundingClientRect();
            if (rect.width * rect.height >= 160 * 90) {
                confidence += 3;
                reasons.push('large');
            }
        } catch (error) {}
        if (video.ended) {
            confidence -= 20;
        }
    }

    if (reasons.length === 0) {
        reasons.push('base');
    }

    return {
        url: normalized,
        confidence: Math.max(0, Math.min(100, Math.round(confidence))),
        reasons: [...new Set(reasons)],
        sources: sources.length > 0 ? sources : ['UNKNOWN'],
    };
}

function mergeVideoCandidates(candidates) {
    const merged = new Map();
    for (const candidate of candidates.filter(Boolean)) {
        const existing = merged.get(candidate.url);
        if (!existing) {
            merged.set(candidate.url, {
                url: candidate.url,
                confidence: candidate.confidence,
                reasons: [...candidate.reasons],
                sources: [...candidate.sources],
            });
            continue;
        }
        existing.confidence = Math.max(existing.confidence, candidate.confidence);
        existing.reasons = [...new Set([...existing.reasons, ...candidate.reasons])];
        existing.sources = [...new Set([...existing.sources, ...candidate.sources])];
    }
    return [...merged.values()].sort((a, b) => b.confidence - a.confidence || a.url.localeCompare(b.url));
}

async function collectValidVideoCandidates(parser, items) {
    const grouped = new Map();
    for (const item of items) {
        const url = normalizeVideoUrl(item && item.url);
        if (!url) {
            continue;
        }
        if (!grouped.has(url)) {
            grouped.set(url, []);
        }
        grouped.get(url).push({ url: url, source: item.source, video: item.video });
    }

    const candidates = [];
    for (const [url, groupedItems] of grouped) {
        if (!await parser.check(url)) {
            continue;
        }
        for (const item of groupedItems) {
            candidates.push(createVideoCandidate(url, item.source, item.video));
        }
    }
    return mergeVideoCandidates(candidates);
}

async function selectVideoCandidate(candidates) {
    currentMedia.video = undefined;
    if (candidates.length === 0) {
        return;
    }
    if (candidates.length === 1) {
        currentMedia.video = candidates[0].url;
        return;
    }
    // Confidence only orders the choices. Multiple candidates are never auto-selected.
    currentMedia.video = await showLinkSelectionModal(candidates);
}

function videoSourceText(zh, en) {
    return currentConfig.global.language === 'zh' ? zh : en;
}

function getVideoConfidenceLevel(confidence) {
    if (confidence >= 80) {
        return videoSourceText('高', 'High');
    }
    if (confidence >= 60) {
        return videoSourceText('中', 'Medium');
    }
    return videoSourceText('低', 'Low');
}

function getVideoReasonLabel(reason) {
    const labels = {
        playing: ['正在播放', 'Playing'],
        progress: ['已有播放进度', 'Playback progress'],
        visible: ['页面可见', 'Visible on page'],
        ready: ['已可播放', 'Ready to play'],
        large: ['主要播放器尺寸', 'Large player'],
        hls: ['HLS 视频流', 'HLS stream'],
        direct: ['直接媒体链接', 'Direct media URL'],
        network: ['网络请求捕获', 'Captured from network request'],
        base: ['符合视频链接规则', 'URL matched parser rules'],
    };
    const label = labels[reason] || [reason, reason];
    return videoSourceText(label[0], label[1]);
}

var requestHooksInstalled = false;
var activeRequestVideoCollector;
var suppressRequestVideoCapture = 0;

function captureRequestVideoUrls(value, sourceName) {
    if (!activeRequestVideoCollector || suppressRequestVideoCapture > 0) {
        return;
    }
    for (const url of extractVideoUrls(value)) {
        activeRequestVideoCollector.recordVideo(url, sourceName);
    }
}

function installRequestVideoHooks() {
    if (requestHooksInstalled) {
        return;
    }
    requestHooksInstalled = true;

    const open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url, async, user, password) {
        captureRequestVideoUrls(url, 'XHR');
        return open.apply(this, arguments);
    };

    const originalFetch = window.fetch;
    window.fetch = function (url, options) {
        captureRequestVideoUrls(url, 'FETCH');
        return originalFetch.apply(this, arguments);
    };
}

'''
replace_once(marker, marker + helpers, "candidate helpers")

new_parse_check = r'''    async parseTime() {
        try {
            const videos = Array.from(document.getElementsByTagName('video'));
            if (videos.length === 0) {
                return;
            }
            let video = currentMedia.video ?
                videos.find(item => getVideoElementUrls(item).includes(currentMedia.video)) :
                undefined;
            video = video || videos.find(item => !item.paused && !item.ended) || videos[0];
            currentMedia.time = video.currentTime;
        } catch (error) {
            console.error("获取开始时间失败", error);
        }
    }
    async check(video) {
        video = normalizeVideoUrl(video || currentMedia.video);
        if (!video || video.startsWith('https://www.mp4')) {
            return false;
        }

        this.videoCheckCache = this.videoCheckCache || new Map();
        if (this.videoCheckCache.has(video)) {
            return this.videoCheckCache.get(video);
        }

        const resultPromise = (async () => {
            if (/\.m3u8?(?:$|[?#])/i.test(video)) {
                suppressRequestVideoCapture++;
                try {
                    const response = await fetch(video, {
                        method: 'GET',
                        credentials: 'include'
                    });
                    if (response.ok) {
                        const body = await response.text();
                        if (body && body.includes('#EXTM3U')) {
                            return body.toLowerCase().indexOf('.png') === -1;
                        }
                    }
                } catch (error) {
                } finally {
                    suppressRequestVideoCapture--;
                }
            }
            return new RegExp(VIDEO_URL_REGEX_EXACT).test(video);
        })();

        this.videoCheckCache.set(video, resultPromise);
        return resultPromise;
    }
'''
source, count = re.subn(
    r"    async parseTime\(\) \{.*?\n    async pause\(\) \{",
    new_parse_check + "    async pause() {",
    source,
    count=1,
    flags=re.S,
)
if count != 1:
    raise RuntimeError(f"BaseParser parseTime/check: expected one replacement, got {count}")

replace_once(
    """                } catch (error) {
                    latestError = error;
                    console.error(`第${currentTryCount}次尝试解析失败：`, error);
                }
""",
    """                } catch (error) {
                    if (error && error.name === 'VideoSelectionCancelledError') {
                        return;
                    }
                    latestError = error;
                    console.error(`第${currentTryCount}次尝试解析失败：`, error);
                }
""",
    "cancel retry handling",
)

parser_block = r'''    VIDEO: class Parser extends BaseParser {
        async execute() {
            await this.parseVideo();
            await this.parseTitle();
            await this.parseTime();
        }
        async parseVideo() {
            const items = [];
            for (const video of document.getElementsByTagName('video')) {
                for (const url of getVideoElementUrls(video)) {
                    items.push({ url: url, source: 'VIDEO', video: video });
                }
            }
            const candidates = await collectValidVideoCandidates(this, items);
            await selectVideoCandidate(candidates);
        }
        async check(video) {
            return Boolean(normalizeVideoUrl(video || currentMedia.video));
        }
    },
    URL: class Parser extends BaseParser {
        async execute() {
            await this.parseVideo();
            await this.parseTitle();
            await this.parseTime();
        }
        async parseVideo() {
            const items = extractVideoUrls(currentUrl).map(url => ({ url: url, source: 'URL' }));
            for (const iframe of document.getElementsByTagName('iframe')) {
                for (const url of extractVideoUrls(iframe.src)) {
                    items.push({ url: url, source: 'IFRAME' });
                }
            }
            const candidates = await collectValidVideoCandidates(this, items);
            await selectVideoCandidate(candidates);
        }
    },
    HTML: class Parser extends BaseParser {
        async execute() {
            await this.parseVideo();
            await this.parseTitle();
            await this.parseTime();
        }
        async parseVideo() {
            const html = document.body ? document.body.innerHTML : '';
            const items = extractVideoUrls(html).map(url => ({ url: url, source: 'HTML' }));
            const candidates = await collectValidVideoCandidates(this, items);
            await selectVideoCandidate(candidates);
        }
    },
    SCRIPT: class Parser extends BaseParser {
        async execute() {
            await this.parseVideo();
            await this.parseTitle();
            await this.parseTime();
        }
        async parseVideo() {
            const items = [];
            for (const script of document.scripts) {
                for (const url of extractVideoUrls(script.innerHTML)) {
                    items.push({ url: url, source: 'SCRIPT' });
                }
            }
            const candidates = await collectValidVideoCandidates(this, items);
            await selectVideoCandidate(candidates);
        }
    },
    REQUEST: class Parser extends BaseParser {
        constructor() {
            super();
            this.videos = new Map();
            activeRequestVideoCollector = this;
            installRequestVideoHooks();
        }
        recordVideo(url, sourceName) {
            const normalized = normalizeVideoUrl(url);
            if (!normalized) {
                return;
            }
            const now = Date.now();
            const existing = this.videos.get(normalized) || {
                timestamp: now,
                sources: new Set(),
            };
            existing.timestamp = now;
            existing.sources.add(sourceName || 'REQUEST');
            this.videos.set(normalized, existing);
        }
        getRecentVideoItems() {
            const cutoff = Date.now() - REQUEST_CANDIDATE_TTL;
            const items = [];
            for (const [url, info] of this.videos) {
                if (info.timestamp < cutoff) {
                    this.videos.delete(url);
                    continue;
                }
                items.push({ url: url, source: [...info.sources] });
            }
            return items;
        }
        async execute() {
            await this.parseTitle();
            await this.parseVideo();
            await this.parseReferer();
            await this.parseTime();
        }
        async parseVideo() {
            const candidates = await collectValidVideoCandidates(this, this.getRecentVideoItems());
            await selectVideoCandidate(candidates);
        }
    },
'''
source, count = re.subn(
    r"    VIDEO: class Parser extends BaseParser \{.*?\n    BILIBILI: class Parser extends BaseParser \{",
    parser_block + "    BILIBILI: class Parser extends BaseParser {",
    source,
    count=1,
    flags=re.S,
)
if count != 1:
    raise RuntimeError(f"parser block: expected one replacement, got {count}")

modal = r'''function showLinkSelectionModal(candidates) {
    return new Promise((resolve, reject) => {
        const MODAL_DIV_ID = `${PROJECT_NAME}-modal-div`;
        let modalDiv = document.getElementById(MODAL_DIV_ID);
        if (modalDiv) {
            if (typeof modalDiv.__externalPlayerCancel === 'function') {
                modalDiv.__externalPlayerCancel();
            } else {
                modalDiv.remove();
            }
        }

        const sortedCandidates = mergeVideoCandidates(candidates.map(candidate =>
            typeof candidate === 'string' ? createVideoCandidate(candidate, 'UNKNOWN') : candidate
        ));

        modalDiv = document.createElement('div');
        modalDiv.id = MODAL_DIV_ID;
        modalDiv.style.position = 'fixed';
        modalDiv.style.inset = '0';
        modalDiv.style.backgroundColor = 'rgba(0, 0, 0, 0.55)';
        modalDiv.style.zIndex = FIRST_Z_INDEX;
        modalDiv.style.display = 'flex';
        modalDiv.style.justifyContent = 'center';
        modalDiv.style.alignItems = 'center';
        modalDiv.style.padding = '20px';
        modalDiv.style.boxSizing = 'border-box';

        const contentDiv = document.createElement('div');
        contentDiv.style.backgroundColor = '#fff';
        contentDiv.style.padding = '20px';
        contentDiv.style.borderRadius = '10px';
        contentDiv.style.width = 'min(760px, 92vw)';
        contentDiv.style.maxHeight = '82vh';
        contentDiv.style.overflow = 'auto';
        contentDiv.style.boxShadow = '0 8px 30px rgba(0, 0, 0, 0.25)';
        contentDiv.addEventListener('click', event => event.stopPropagation());

        const title = document.createElement('h3');
        title.textContent = videoSourceText('选择视频源', 'Select video source');
        title.style.margin = '0';
        title.style.color = COLOR.TEXT;
        contentDiv.appendChild(title);

        const hint = document.createElement('p');
        hint.textContent = videoSourceText(
            '置信度仅为启发式评分，只用于排序。检测到多个视频源时不会自动选择，请手动确认。',
            'Confidence is a heuristic score used only for sorting. Multiple sources are never selected automatically.'
        );
        hint.style.margin = '8px 0 0';
        hint.style.color = COLOR.TEXT;
        hint.style.opacity = '0.75';
        hint.style.fontSize = '13px';
        hint.style.lineHeight = '1.5';
        contentDiv.appendChild(hint);

        const divider = document.createElement('div');
        divider.style.borderBottom = `1px solid ${COLOR.BORDER}`;
        divider.style.marginTop = '14px';
        contentDiv.appendChild(divider);

        const list = document.createElement('div');
        list.style.display = 'flex';
        list.style.flexDirection = 'column';
        list.style.gap = '10px';
        list.style.marginTop = '15px';

        sortedCandidates.forEach((candidate, index) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.style.padding = '12px';
            btn.style.border = `1px solid ${COLOR.BORDER}`;
            btn.style.borderRadius = '8px';
            btn.style.backgroundColor = '#fff';
            btn.style.color = COLOR.TEXT;
            btn.style.cursor = 'pointer';
            btn.style.textAlign = 'left';
            btn.style.width = '100%';
            btn.style.boxSizing = 'border-box';

            const header = document.createElement('div');
            header.style.display = 'flex';
            header.style.alignItems = 'center';
            header.style.justifyContent = 'space-between';
            header.style.gap = '12px';

            const sourceLabel = document.createElement('strong');
            sourceLabel.textContent = `#${index + 1} · ${videoSourceText('来源', 'Source')}: ${candidate.sources.join(' / ')}`;
            sourceLabel.style.color = COLOR.TEXT;
            header.appendChild(sourceLabel);

            const confidence = document.createElement('span');
            confidence.textContent = `${videoSourceText('置信度', 'Confidence')} ${candidate.confidence}% · ${getVideoConfidenceLevel(candidate.confidence)}`;
            confidence.style.flexShrink = '0';
            confidence.style.padding = '3px 8px';
            confidence.style.borderRadius = '999px';
            confidence.style.backgroundColor = candidate.confidence >= 60 ? COLOR.PRIMARY : COLOR.WARNING;
            confidence.style.color = COLOR.TEXT_ACTIVE;
            confidence.style.fontSize = '12px';
            confidence.style.fontWeight = 'bold';
            header.appendChild(confidence);
            btn.appendChild(header);

            const reasons = document.createElement('div');
            reasons.textContent = candidate.reasons.map(getVideoReasonLabel).join(' · ');
            reasons.style.marginTop = '7px';
            reasons.style.fontSize = '12px';
            reasons.style.opacity = '0.75';
            btn.appendChild(reasons);

            const url = document.createElement('div');
            url.textContent = candidate.url;
            url.title = candidate.url;
            url.style.marginTop = '8px';
            url.style.color = COLOR.PRIMARY;
            url.style.fontSize = '13px';
            url.style.wordBreak = 'break-all';
            url.style.lineHeight = '1.4';
            btn.appendChild(url);

            btn.addEventListener('mouseover', () => {
                btn.style.borderColor = COLOR.PRIMARY;
                btn.style.boxShadow = `0 0 0 1px ${COLOR.PRIMARY}`;
            });
            btn.addEventListener('mouseout', () => {
                btn.style.borderColor = COLOR.BORDER;
                btn.style.boxShadow = 'none';
            });
            btn.addEventListener('click', () => finish(candidate.url));
            list.appendChild(btn);
        });
        contentDiv.appendChild(list);

        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.textContent = videoSourceText('取消', 'Cancel');
        closeBtn.style.marginTop = '18px';
        closeBtn.style.padding = '9px 18px';
        closeBtn.style.border = 'none';
        closeBtn.style.borderRadius = '5px';
        closeBtn.style.backgroundColor = COLOR.WARNING;
        closeBtn.style.color = COLOR.TEXT_ACTIVE;
        closeBtn.style.cursor = 'pointer';
        closeBtn.style.float = 'right';
        closeBtn.addEventListener('click', cancel);
        contentDiv.appendChild(closeBtn);

        let settled = false;
        const keydownHandler = event => {
            if (event.key === 'Escape') {
                event.preventDefault();
                cancel();
            }
        };

        function cleanup() {
            document.removeEventListener('keydown', keydownHandler, true);
            if (modalDiv && modalDiv.isConnected) {
                modalDiv.remove();
            }
        }

        function finish(url) {
            if (settled) {
                return;
            }
            settled = true;
            cleanup();
            resolve(url);
        }

        function cancel() {
            if (settled) {
                return;
            }
            settled = true;
            cleanup();
            reject(new VideoSelectionCancelledError());
        }

        modalDiv.__externalPlayerCancel = cancel;
        modalDiv.addEventListener('click', cancel);
        document.addEventListener('keydown', keydownHandler, true);
        modalDiv.appendChild(contentDiv);
        document.body.appendChild(modalDiv);
    });
}
'''
source, count = re.subn(
    r"function showLinkSelectionModal\(urls\) \{.*?\n\}\n\nfunction appendButtonDiv\(\) \{",
    modal + "\nfunction appendButtonDiv() {",
    source,
    count=1,
    flags=re.S,
)
if count != 1:
    raise RuntimeError(f"modal: expected one replacement, got {count}")

replace_once(
    """                console.log(`match parser regex: ${new RegExp(regex)}\n${url}`);
                return new PARSER[key.replace(/[A-Z]/g, letter => `_${letter}`).toUpperCase()]();
""",
    """                console.log(`match parser regex: ${new RegExp(regex)}\n${url}`);
                const parserInstance = new PARSER[key.replace(/[A-Z]/g, letter => `_${letter}`).toUpperCase()]();
                activeRequestVideoCollector = parserInstance instanceof PARSER.REQUEST ? parserInstance : undefined;
                return parserInstance;
""",
    "request collector lifecycle",
)

PATH.write_text(source, encoding="utf-8")
print("Applied video source confidence UX")
