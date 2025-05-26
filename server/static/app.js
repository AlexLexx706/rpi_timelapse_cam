/* =======================================================================
 *  Socket.IO
 * ==================================================================== */
const socket = io({ transports: ["websocket"] });

function updateProgress(v) {
    const fill = document.getElementById("progressFill");
    if (fill) {
        fill.style.width = v + "%";
    }
}

/* Recalculate layouts on any viewport change (rotation / resize) */
webix.event(window, "resize", () => webix.ui.resize());

/* =======================================================================
 *  Main UI
 * ==================================================================== */
webix.ready(() => {

    /* Mobile-friendly skin tweaks */
    if (webix.env.mobile) {
        const s = webix.skin.$active;
        s.inputHeight = 42;
        s.barHeight = 46;
        s.fontSize = 16;
    }

    webix.ui({
        container: "root",
        rows: [
            {
                view: "toolbar",
                css: "topbar",
                padding: 5,
                cols: [
                    {
                        view: "button",
                        id: "tlStartBtn",
                        value: "Start Timelapse",
                        width: 160,
                        css: "webix_primary",
                        state: "stopped",  // custom flag
                        click() {
                            const btn = this;
            
                            if (btn.config.state === "stopped") {
                                // Стартуем съёмку
                                webix.ajax()
                                    .post("/api/timelapse/start")
                                    .then(() => {
                                        btn.define({ value: "Stop Timelapse", type: "danger" });
                                        btn.config.state = "started";
                                        btn.refresh();
                                        webix.message("Timelapse started");
                                    })
                                    .catch(() => {
                                        webix.message({ type: "error", text: "Failed to start timelapse" });
                                    });
                            } else {
                                // Останавливаем съёмку
                                webix.ajax()
                                    .post("/api/timelapse/stop")
                                    .then(() => {
                                        btn.define({ value: "Start Timelapse", type: "", css: "webix_primary" });
                                        btn.config.state = "stopped";
                                        btn.refresh();
                                        webix.message("Timelapse stopped");
                                    })
                                    .catch(() => {
                                        webix.message({ type: "error", text: "Failed to stop timelapse" });
                                    });
                            }
                        }
                    }
                ]
            },
            {
                view: "tabbar",
                id: "tabs",
                multiview: true,
                scroll: true,
                optionWidth: 120,
                options: [
                    { id: "filesTab", value: "📂 Images" },
                    { id: "settingsTab", value: "⚙️ Settings" },
                    { id: "streamTab", value: "🎥 Stream" },
                    { id: "videosTab", value: "🎥 Video" }
                ]
            },
            {
                cells: [
                    filesTab(),
                    settingsTab(),
                    streamTab(),
                    videosTab()
                ]
            }
        ]
    });

    /* Realtime events -------------------------------------------------- */
    socket.on("files_updated", data => {
        const table = $$("files");
        if (table) {
            table.clearAll();
            table.parse(data);
        }
    });

    socket.on("popup",
        msg => webix.message({ text: msg, type: "info", expire: 4000 }));

    socket.on("process_progress",
        d => updateProgress(d.progress));

    socket.on("process_done", data => {
        $$("progressBar").hide();
        if (data?.video) {
            const url = "/videos/" + data.video;
            webix.message({
                text: `Processing complete: <a href='${url}' target='_blank'>Download</a>`,
                type: "success",
                expire: 8000
            });
        } else {
            webix.message("Processing complete");
        }
    });

    socket.on("settings_changed", payload => {
        if (payload.origin === socket.id) { return; }          // ignore self
        applyRemoteSettings(payload.settings);
    });

    socket.on("videos_updated", data => {
        const table = $$("videos");
        if (table) {
            table.clearAll();
            table.parse(data);
        }
    });
});

/* =======================================================================
 *  FILES tab
 * ==================================================================== */
function filesTab() {

    return {
        id: "filesTab",
        rows: [
            fileToolbar(),
            {
                view: "datatable",
                id: "files",
                url: "/api/files",        // auto-load JSON
                select: "row",
                scroll: "y",

                columns: [
                    {
                        id: "sel",
                        header: { content: "masterCheckbox" },
                        template: "{common.checkbox()}",
                        checkValue: "on",
                        width: 40
                    },
                    {
                        id: "thumb",
                        header: "",
                        width: 48,
                        template: d => `<img src='${d.thumb}' width=40>`
                    },
                    {
                        id: "name",
                        fillspace: true
                    }
                ],

                on: {
                    onItemDblClick(id) {
                        const it = this.getItem(id);
                        const fullURL = `/files/${it.name}`;

                        const win = webix.ui({
                            view: "window",
                            id: "imgViewer",
                            modal: true,
                            fullscreen: true,
                            head: false,
                            body: {
                                template:
                                    `<img src="${fullURL}"
                                          style="width:100%;height:100%;
                                                 object-fit:contain;display:block;">`
                            }
                        });

                        webix.UIManager.addHotKey("esc", () => win.hide(), win);
                        win.show();
                    }
                }
            },
            {
                view: "template",
                id: "progressBar",
                height: 24,
                hidden: true,
                template: `
                    <div style="
                        position:relative;
                        height:100%;
                        width:100%;
                        background:#eee;
                        overflow:hidden;
                        border-radius:4px;
                    ">
                        <div id="progressFill" style="
                            position:absolute;
                            height:100%;
                            width:0%;
                            background:#28b;
                            transition:width 0.3s;
                            z-index:1;
                        "></div>
                        <div style="
                            position:absolute;
                            top:0; left:0; right:0; bottom:0;
                            display:flex;
                            align-items:center;
                            justify-content:center;
                            font-weight:bold;
                            font-size:14px;
                            color:#000;
                            z-index:2;
                            pointer-events:none;
                        ">
                            Processing files...
                        </div>
                    </div>
                `
            }
        ]
    };
}

/* toolbar above the datatable ------------------------------------------ */
function fileToolbar() {

    const getChecked = () =>
        $$("files").data.serialize()
            .filter(r => r.sel === "on")
            .map(r => r.name);

    return {
        view: "toolbar",
        cols: [
            {
                view: "button",
                label: "Process",
                css: "webix_primary",
                width: 120,
                click() {
                    const files = getChecked();
                    if (!files.length) { return; }

                    $$("progressBar").show();
                    webix.delay(() => updateProgress(0));

                    webix.ajax()
                        .headers({ "Content-Type": "application/json" })
                        .post("/api/process", JSON.stringify({ files }));
                }
            },
            {
                view: "button",
                label: "Delete",
                type: "danger",
                width: 120,
                click() {
                    const files = getChecked();
                    if (!files.length) { return; }

                    webix.confirm("Delete selected files?")
                        .then(() =>
                            webix.ajax()
                                .headers({ "Content-Type": "application/json" })
                                .post(
                                    "/api/delete",
                                    JSON.stringify({ files }))
                        );
                }
            }
        ]
    };
}

/* =======================================================================
 *  SETTINGS tab
 * ==================================================================== */
function settingsTab() {
    let original = {};

    return {
        id: "settingsTab",
        rows: [
            {
                view: "form",
                id: "settingsForm",
                url: "/api/settings",
                on: {
                    onAfterLoad() {
                        original = this.getValues();
                    }
                },
                rows: [
                    {
                        view: "fieldset",
                        label: "Camera Settings",
                        body: {
                            rows: [
                                {
                                    view: "richselect",
                                    name: "resolution",
                                    label: "Resolution",
                                    labelWidth: 120,
                                    options: ["640x480", "1280x720", "1920x1080"]
                                },
                                {
                                    view: "slider",
                                    name: "brightness",
                                    label: "Brightness",
                                    labelWidth: 120,
                                    min: 0,
                                    max: 100
                                },
                                {
                                    view: "slider",
                                    name: "contrast",
                                    label: "Contrast",
                                    labelWidth: 120,
                                    min: 0,
                                    max: 100
                                },
                                {
                                    view: "slider",
                                    name: "focus",
                                    label: "Focus",
                                    labelWidth: 120,
                                    min: 0,
                                    max: 100
                                },
                                {
                                    view: "counter",
                                    name: "exposure",
                                    label: "Exposure (ms)",
                                    labelWidth: 120,
                                    min: -100,
                                    max: 100,
                                    step: 1
                                },
                                {
                                    view: "counter",
                                    name: "fps",
                                    label: "FPS",
                                    labelWidth: 120,
                                    min: 1,
                                    max: 100,
                                    step: 1
                                }
                            ]
                        }
                    },
                    {
                        view: "fieldset",
                        label: "Timelapse Settings",
                        body: {
                            rows: [
                                {
                                    view: "datepicker",
                                    name: "tl_start",
                                    type: "time",
                                    label: "Start time",
                                    format: "%H:%i:%s",
                                    stringResult: true,
                                    labelWidth: 120
                                },
                                {
                                    view: "datepicker",
                                    name: "tl_stop",
                                    type: "time",
                                    label: "Stop time",
                                    format: "%H:%i:%s",
                                    stringResult: true,
                                    labelWidth: 120
                                },
                                {
                                    view: "counter",
                                    name: "tl_interval_sec",
                                    label: "Interval (sec)",
                                    value: 60,
                                    min: 1,
                                    max: 3600,
                                    step: 1,
                                    labelWidth: 120
                                },
                                {
                                    view: "checkbox",
                                    name: "tl_mode",
                                    label: "",
                                    labelRight: "Continuous mode"
                                }
                            ]
                        }
                    },
                    {
                        margin: 10,
                        cols: [
                            {
                                view: "button",
                                value: "Apply",
                                css: "webix_primary",
                                click() {
                                    const form = this.getFormView();
                                    const data = form.getValues();

                                    webix.ajax()
                                        .headers({ "Content-Type": "application/json" })
                                        .post("/api/settings", JSON.stringify(data))
                                        .then(() => {
                                            original = data;
                                            webix.message("Saved");
                                        });
                                }
                            },
                            {
                                view: "button",
                                value: "Cancel",
                                click() {
                                    this.getFormView().setValues(original);
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    };
}


/* =======================================================================
 *  STREAM tab
 * ==================================================================== */
function streamTab() {

    const startStopBtn = {
        view: "button",
        id: "streamToggle",
        width: 160,
        css: "webix_primary",
        label: "Start",
        streaming: false,
        click() {
            const img = document.getElementById("streamViewer");

            if (!this.streaming) {
                img.src = "/mjpeg?" + Date.now();
                this.streaming = true;
                this.define({ label: "Stop", type: "danger" });
            } else {
                img.src = "";
                this.streaming = false;
                this.define({ label: "Start", css: "webix_primary", type: "" });
            }
            this.refresh();
        }
    };

    return {
        id: "streamTab",
        rows: [
            {
                view: "template",
                id: "streamImg",
                template: "<img id='streamViewer' " +
                    "style='width:100%;height:100%;object-fit:contain' " +
                    "src=''>"
            },
            {
                view: "toolbar",
                cols: [
                    {
                        view: "slider",
                        id: "brightnessSlider",
                        label: "Brightness",
                        value: 50,
                        min: 0,
                        max: 100,
                        on: { onChange: v => socket.emit("set_brightness", v) }
                    },
                    {
                        view: "slider",
                        id: "contrastSlider",
                        label: "Contrast",
                        value: 50,
                        min: 0,
                        max: 100,
                        on: { onChange: v => socket.emit("set_contrast", v) }
                    }
                ]
            },
            {
                view: "toolbar",
                css: "startbar",
                cols: [{}, startStopBtn, {}]
            }
        ]
    };
}


function videosTab() {

    const getChecked = () => {
        const table = $$("videos");
        if (!table) return [];
        return table.data.serialize()
            .filter(r => r.sel === "on")
            .map(r => r.name);
    };

    return {
        id: "videosTab",
        rows: [
            {
                view: "toolbar",
                cols: [
                    {
                        view: "button",
                        value: "Delete",
                        type: "danger",
                        width: 120,
                        click() {
                            const files = getChecked();
                            if (!files.length) return;

                            webix.confirm("Delete selected videos?").then(() => {
                                webix.ajax()
                                    .headers({ "Content-Type": "application/json" })
                                    .post("/api/videos/delete", JSON.stringify({ files }));
                            });
                        }
                    },
                    { view: "label", label: "Double-click to play" }
                ]
            },
            {
                view: "datatable",
                id: "videos",
                url: "/api/videos",      // ← загружаем данные напрямую
                select: "row",
                columns: [
                    {
                        id: "sel",
                        header: { content: "masterCheckbox" },
                        template: "{common.checkbox()}",
                        checkValue: "on",
                        width: 40
                    },
                    {
                        id: "name",
                        header: "Name",
                        fillspace: true
                    },
                    {
                        id: "size",
                        header: "Size",
                        width: 100,
                        template: v => humanSize(v.size)
                    }
                ],
                on: {
                    onItemDblClick(id) {
                        const item = this.getItem(id);
                        window.open(item.url, "_blank");
                    }
                }
            }
        ]
    };
}

function humanSize(bytes) {
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return bytes.toFixed(1) + " " + units[i];
}


/* =======================================================================
 *  Apply settings coming from other clients
 * ==================================================================== */
function applyRemoteSettings(settings) {

    const form = $$("#settingsForm");
    if (form) {
        const cur = form.getValues();
        form.setValues({
            resolution: settings.resolution ?? cur.resolution,
            brightness: settings.brightness ?? cur.brightness,
            contrast: settings.contrast ?? cur.contrast,
            focus: settings.focus ?? cur.focus,
            exposure: settings.exposure ?? cur.exposure,
            fps: settings.fps ?? cur.fps,
            tl_start: settings.tl_start ?? cur.tl_start,
            tl_stop: settings.tl_stop ?? cur.tl_stop,
            tl_interval_sec: settings.tl_interval_sec ?? cur.tl_interval_sec,
            tl_mode: settings.tl_mode ?? cur.tl_mode,
        }, true);
    }

    if ($$("#brightnessSlider") && settings.brightness !== undefined) {
        $$("#brightnessSlider").setValue(settings.brightness, true);
    }
    if ($$("#contrastSlider") && settings.contrast !== undefined) {
        $$("#contrastSlider").setValue(settings.contrast, true);
    }

    webix.message({
        text: "Settings changed by another user",
        type: "info",
        expire: 3000
    });
}
