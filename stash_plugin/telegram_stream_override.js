
(function () {
    let activeSceneId = null;

    if (!window.PluginApi) {
        console.error("[Telegram Override] PluginApi unavailable");
        return;
    }

    let targetSceneActive = false;
    const patchedPlayers = new WeakSet();
    const appliedStarts = new WeakMap();

    // Handle timeline seeks when Stash reloads the stream with ?start=.
    document.addEventListener("loadedmetadata", function (event) {
        if (!targetSceneActive) return;

        const video = event.target;
        if (!(video instanceof HTMLVideoElement)) return;

        const source = video.currentSrc || video.src;
        if (!source) return;

        let url;

        try {
            url = new URL(source, window.location.href);
        } catch (_) {
            return;
        }

        const streamMatch = url.pathname.match(/\/smart-stream\/(\d+)(?:\/|$)/);
        if (!streamMatch || streamMatch[1] !== activeSceneId) return;

        const start = Number(url.searchParams.get("start"));

        if (!Number.isFinite(start) || start <= 1) return;
        if (appliedStarts.get(video) === source) return;

        setTimeout(() => {
            if (!targetSceneActive || !video.isConnected) return;
            if ((video.currentSrc || video.src) !== source) return;

            if (
                Number.isFinite(video.duration) &&
                start >= video.duration
            ) return;

            if (Math.abs(video.currentTime - start) <= 2) {
                appliedStarts.set(video, source);
                return;
            }

            video.currentTime = start;
            appliedStarts.set(video, source);

            console.log(
                "[Telegram Override] Applied seek:",
                start
            );
        }, 0);
    }, true);

    // Intercept the actual Video.js source initialization.
    function patchPlayer(player) {
        if (!targetSceneActive || !player || patchedPlayers.has(player)) {
            return;
        }

        if (typeof player.src !== "function") return;

        const originalSrc = player.src;

        player.src = function (source) {
            if (arguments.length === 0) {
                return originalSrc.call(this);
            }

            function removeOffset(item) {
                if (!item || typeof item !== "object") return item;

                return {
                    ...item,
                    offset: false
                };
            }

            let fixedSource = source;

            if (Array.isArray(source)) {
                fixedSource = source.map(removeOffset);
            } else if (source && typeof source === "object") {
                fixedSource = removeOffset(source);
            }

            if (fixedSource !== source) {
                console.log(
                    "[Telegram Override] Forcing Video.js offset=false",
                    fixedSource
                );
            }

            return originalSrc.call(this, fixedSource);
        };

        patchedPlayers.add(player);

        // Correct the source if Stash initialized it before our interception.
        if (typeof player.currentSource === "function") {
            const current = player.currentSource();

            if (current && current.offset === true) {
                console.log(
                    "[Telegram Override] Correcting existing source offset"
                );

                player.src({
                    ...current,
                    offset: false
                });
            }
        }

        console.log("[Telegram Override] Video.js source interception installed");
    }

    function findPlayers() {
        if (!targetSceneActive) return;

        document.querySelectorAll(".video-js").forEach(element => {
            if (element.player) {
                patchPlayer(element.player);
            }
        });
    }

    const observer = new MutationObserver(findPlayers);

    function startObserver() {
        if (!document.body) {
            requestAnimationFrame(startObserver);
            return;
        }

        observer.observe(document.body, {
            childList: true,
            subtree: true
        });

        findPlayers();
    }

    startObserver();

    // Override the scene stream.
    window.PluginApi.patch.before("ScenePlayer", function (...args) {
        const props = args[0];
        const scene = props?.scene;

        if (!scene) return args;

        if (scene.id === undefined || scene.id === null) {
            targetSceneActive = false;
            activeSceneId = null;
            return args;
        }

        targetSceneActive = true;
        activeSceneId = String(scene.id);

        const smartUrl = `http://127.0.0.1:9998/smart-stream/${activeSceneId}`;

        const updatedScene = {
            ...scene,
            sceneStreams: [{
                url: smartUrl,
                mime_type: "video/mp4",
                label: "Local first, Telegram fallback"
            }]
        };

        const updatedProps = {
            ...props,
            scene: updatedScene
        };

        // The player may already exist when ScenePlayer renders again.
        findPlayers();

        return [
            { ...updatedProps, scene: updatedScene },
            ...args.slice(1)
        ];
    });

    console.log(
        "[Telegram Override] Local-first mode with permanent offset fix installed"
    );
})();

