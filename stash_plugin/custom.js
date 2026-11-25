
(function () {
    "use strict";

    const BUTTON_CLASS = "stash-controls-toggle";
    const FLIP_BUTTON_CLASS = "stash-flip-toggle";
    const HIDDEN_CLASS = "stash-controls-hidden";

    let vrZoom = 1.0;
    let flipX = false;
    let flipY = false;

    const ZOOM_STEP = 0.10;
    const MIN_ZOOM = 0.50;
    const MAX_ZOOM = 2.00;
    const BASE_FOV = 75;

    // ==========================================
    // CSS
    // ==========================================

    function addStyles() {
        if (document.getElementById("stash-custom-vr-style"))
            return;

        const style = document.createElement("style");
        style.id = "stash-custom-vr-style";

        style.textContent = `
            .${HIDDEN_CLASS} .vjs-control-bar {
                display: none !important;
            }

            .${BUTTON_CLASS},
            .${FLIP_BUTTON_CLASS} {
                position: absolute !important;
                right: 20px !important;
                width: 42px !important;
                height: 42px !important;
                z-index: 999999 !important;
                border: 0 !important;
                border-radius: 50% !important;
                background: rgba(0,0,0,.65) !important;
                color: white !important;
                cursor: pointer !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                font-size: 20px !important;
                opacity: .45;
                transition: opacity .2s;
            }

            .${BUTTON_CLASS} {
                bottom: 20px !important;
            }

            .${FLIP_BUTTON_CLASS} {
                bottom: 70px !important;
            }

            .${BUTTON_CLASS}:hover,
            .${FLIP_BUTTON_CLASS}:hover {
                opacity: 1 !important;
            }

            .${FLIP_BUTTON_CLASS}.active {
                background: rgba(0,120,255,.85) !important;
                opacity: 1 !important;
            }
        `;

        document.head.appendChild(style);
    }

    // ==========================================
    // VIDEO FLIP
    // ==========================================

    function getVideo(player) {
        return player
            ? player.querySelector("video")
            : null;
    }

    function applyFlip(player) {
        const video = getVideo(player);
        if (!video) return;

        video.style.transform =
            `scale(${flipX ? -1 : 1}, ${flipY ? -1 : 1})`;
        video.style.transformOrigin = "center center";

        const button = player.querySelector(
            "." + FLIP_BUTTON_CLASS
        );

        if (button) {
            button.classList.toggle(
                "active",
                flipX || flipY
            );

            button.title =
                `Flip: ${flipX ? "Horizontal ON" : "Horizontal OFF"} | ` +
                `${flipY ? "Vertical ON" : "Vertical OFF"}\n` +
                "Click: Horizontal | Shift+Click: Vertical | Double-click: Reset";
        }
    }

    function createFlipButton(player) {
        if (player.querySelector("." + FLIP_BUTTON_CLASS))
            return;

        const button = document.createElement("button");
        button.type = "button";
        button.className = FLIP_BUTTON_CLASS;
        button.textContent = "⇋";
        button.title =
            "Click: Horizontal flip | Shift+Click: Vertical flip | Double-click: Reset";
        button.setAttribute("aria-label", "Flip video");

        button.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();

            if (event.shiftKey) {
                flipY = !flipY;
            } else {
                flipX = !flipX;
            }

            applyFlip(player);
        });

        button.addEventListener("dblclick", function (event) {
            event.preventDefault();
            event.stopPropagation();

            flipX = false;
            flipY = false;
            applyFlip(player);
        });

        player.appendChild(button);
    }

    // ==========================================
    // GET STASH PLAYER
    // ==========================================

    function getPlayer() {
        const element = document.querySelector("video-js");
        if (!element) return null;
        return element.player || null;
    }

    // ==========================================
    // GET VR PLUGIN
    // ==========================================

    function getVR() {
        const player = getPlayer();
        if (!player) return null;

        try {
            if (typeof player.vr !== "function")
                return null;

            return player.vr();
        } catch (error) {
            console.log("Stash VR error:", error);
            return null;
        }
    }

    // ==========================================
    // GET VR CAMERA
    // ==========================================

    function getCamera() {
        const vr = getVR();
        if (!vr) return null;
        return vr.camera || null;
    }

    // ==========================================
    // APPLY ZOOM
    // ==========================================

    function applyZoom() {
        const camera = getCamera();
        if (!camera || typeof camera.fov !== "number")
            return;

        camera.fov = Math.max(
            25,
            Math.min(110, BASE_FOV / vrZoom)
        );

        if (typeof camera.updateProjectionMatrix === "function") {
            camera.updateProjectionMatrix();
        }

        console.log(
            "VR Zoom:",
            vrZoom.toFixed(2),
            "FOV:",
            camera.fov.toFixed(2)
        );
    }

    // ==========================================
    // ZOOM COMMANDS
    // ==========================================

    function zoomIn() {
        vrZoom = Math.min(MAX_ZOOM, vrZoom + ZOOM_STEP);
        applyZoom();
    }

    function zoomOut() {
        vrZoom = Math.max(MIN_ZOOM, vrZoom - ZOOM_STEP);
        applyZoom();
    }

    function resetZoom() {
        vrZoom = 1.0;
        applyZoom();
        console.log("VR Zoom reset");
    }

    // ==========================================
    // KEYBOARD COMMANDS
    // ==========================================

    document.addEventListener("keydown", function (event) {
        if (event.key === "F3") {
            const player = document.querySelector("video-js");
            if (!player) return;

            event.preventDefault();
            event.stopPropagation();
            player.classList.toggle(HIDDEN_CLASS);
            return;
        }

        if (event.key === "F4") {
            event.preventDefault();
            event.stopPropagation();
            zoomIn();
            return;
        }

        if (event.key === "F5") {
            event.preventDefault();
            event.stopPropagation();
            zoomOut();
            return;
        }

        if (event.key === "F6") {
            event.preventDefault();
            event.stopPropagation();
            resetZoom();
        }
    }, true);

    // ==========================================
    // CREATE CONTROLS BUTTON
    // ==========================================

    function createButton(player) {
        if (!player) return;

        if (getComputedStyle(player).position === "static") {
            player.style.position = "relative";
        }

        if (!player.querySelector("." + BUTTON_CLASS)) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = BUTTON_CLASS;
            button.textContent = "◉";
            button.title = "Show / Hide Video Controls";

            button.addEventListener("click", function (event) {
                event.preventDefault();
                event.stopPropagation();
                player.classList.toggle(HIDDEN_CLASS);
            });

            player.appendChild(button);
        }

        createFlipButton(player);
        applyFlip(player);
    }

    // ==========================================
    // FIND PLAYERS
    // ==========================================

    function scanPlayers() {
        addStyles();

        document.querySelectorAll("video-js").forEach(function (player) {
            createButton(player);
        });
    }

    // ==========================================
    // WATCH STASH NAVIGATION
    // ==========================================

    const observer = new MutationObserver(function () {
        scanPlayers();
    });

    observer.observe(document.body, {
        childList: true,
        subtree: true
    });

    // ==========================================
    // INITIALIZE
    // ==========================================

    scanPlayers();
    setTimeout(scanPlayers, 500);
    setTimeout(scanPlayers, 1500);
    setTimeout(scanPlayers, 3000);

})();
