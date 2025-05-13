// ------- инициализация ---------------------------------------------
const socket = io({ transports: ["websocket"] });

function updateProgress(v) {
    const barView = $$("progressBar");
    if (!barView) return;                         // view ещё не создан

    const inner = barView.$view.firstElementChild;  // ← сразу готов
    if (!inner) return;                           // защита от крайних случаев

    inner.style.width = v + "%";
}


webix.ready(function () {
    webix.ui({
        container: "root",
        rows: [
            {
                view: "tabbar",
                id: "tabs",
                multiview: true,
                options: [
                    { id: "filesTab", value: "Файлы" },
                    { id: "settingsTab", value: "Настройки" },
                    { id: "streamTab", value: "Трансляция" }
                ]
            },
            {
                cells: [
                    filesTab(),     // функции‑конфигурации ниже
                    settingsTab(),
                    streamTab()
                ]
            }
        ]
    });

    /* ------------ realtime ----------------- */
    socket.on("files_updated", data => {
        $$("files").clearAll().parse(data);
    });

    socket.on("popup", msg => webix.message({ text: msg, type: "info", expire: 4000 }));

    socket.on("process_progress", d => updateProgress(d.progress));
    socket.on("process_done", () => {
        $$("progressBar").hide();
        webix.message("Обработка завершена");
    });
});

// // ---------------- вкладка «Файлы» -----------------------------------
function filesTab() {
    webix.ajax("/api/files").then(resp => {
        $$("files").parse(resp.json());
    });

    return {
        id: "filesTab",
        rows: [
            fileToolbar(),
            {
                view: "datatable",
                id: "files",
                select: "row",
                scroll: "y",
                columns: [
                    {
                        id: "sel",
                        header: { content: "masterCheckbox" },
                        template: "{common.checkbox()}",
                        checkValue: "on"
                    },
                    {
                        id: "thumb",
                        header: "",
                        template: d => `<img src='${d.thumb}' width=40>`,
                        width: 48
                    },
                    {
                        id: "name",
                        fillspace: true
                    }
                ],
                on: {
                    onItemDblClick: function (id) {
                        const item = this.getItem(id);
                        webix.ui.fullScreen();
                        webix.modalbox({ template: `<img src="${item.thumb}" style="width:100%">` });
                    }
                }
            },
            {
                view: "template",
                id: "progressBar",
                height: 4,
                hidden: true,
                template: "<div style='height:100%;width:0%;background:#28b'></div>"
            }
        ]
    };
}

function fileToolbar() {
    return {
        view: "toolbar", cols: [
            {
                view: "button", label: "Обработать", css: "webix_primary", width: 120,
                click: () => {
                    const files = getChecked();
                    if (!files.length) return;
                    $$("progressBar").show();                 // показать и обнулить
                    updateProgress(0);
                    webix.ajax()                                              // создаём Ajax‑объект
                        .headers({ "Content-Type": "application/json" })     // ← нужный заголовок
                        .post("/api/process", JSON.stringify({ files }));    // ← тело‑строка JSON
                }
            },
            {
                view: "button", label: "Удалить", type: "danger", width: 120,
                click: () => {
                    const files = getChecked();
                    if (!files.length) return;
                    webix.confirm("Удалить выбранные?").then(() => {
                        webix.ajax().post("/api/delete", { files });
                    });
                }
            }
        ]
    };

    function getChecked() {
        return $$("files").data.serialize()
            .filter(r => r.sel === "on").map(r => r.name);
    }
    function selectAll(flag) {
        $$("files").data.each(r => r.sel = flag ? "on" : 0);
        $$("files").refresh();
    }
}

// // --------------- вкладка «Настройки» --------------------------------
function settingsTab() {
    let original = {};
    webix.ajax("/api/settings").then(resp => {
        original = resp.json();
        $$("settingsForm").setValues(original);
    });

    return {
        id: "settingsTab",
        rows: [
            {
                view: "form",
                id: "settingsForm",
                elements: [
                    {
                        view: "richselect",
                        name: "res",
                        label: "Разрешение",
                        options: ["640x480", "1280x720", "1920x1080"]
                    },
                    {
                        view: "slider",
                        name: "contrast",
                        label: "Контраст",
                        min: 0,
                        max: 100,
                        value: 50
                    },
                    {
                        view: "slider",
                        name: "focus",
                        label: "Фокус",
                        min: 0,
                        max: 100,
                        value: 50
                    },
                    {
                        view: "text",
                        name: "exposure",
                        label: "Выдержка (мс)",
                        pattern: {
                            mask: "########",
                            allow: /\d/
                        }
                    }
                ]
            },
            {
                cols: [
                    {
                        view: "button",
                        value: "Применить",
                        css: "webix_primary",
                        click: () => {
                            const vals = $$("settingsForm").getValues();

                            webix.ajax()                               // создаём экземпляр
                                .headers({ "Content-Type": "application/json" })   // ставим заголовок
                                .post("/api/settings", JSON.stringify(vals))       // тело‑строка
                                .then(() => {
                                    original = vals;
                                    webix.message("Сохранено");
                                });
                        }
                    },
                    {
                        view: "button",
                        value: "Отмена",
                        click: () => {
                            $$("settingsForm").setValues(original);
                        }
                    }
                ]
            }
        ]
    };
}

// --------------- вкладка «Трансляция» -------------------------------
function streamTab() {

    /* сохраняем флаг прямо в конфиге view‑кнопки */
    const startStopBtn = {
        view: "button",
        id: "streamToggle",
        label: "Начать",
        width: 180,
        css: "webix_primary",
        streaming: false,           // <‑‑ собственное поле‑флаг

        click() {
            const imgTag = document.getElementById("streamViewer");  // само <img>

            if (!this.streaming) {
                /* ----------  ЗАПУСК  ---------- */
                imgTag.src = "/mjpeg?" + Date.now();      // timestamp → no‑cache
                this.streaming = true;
                this.define({ label: "Завершить", type: "danger" });
                this.refresh();

            } else {
                /* ----------  ОСТАНОВКА  ---------- */
                imgTag.src = "";                          // браузер рвёт соединение
                this.streaming = false;
                this.define({ label: "Начать", css: "webix_primary", type: "" });
                this.refresh();
            }
        }
    };

    return {
        id: "streamTab",
        rows: [
            {
                view: "template", id: "streamImg",
                template: "<img id='streamViewer' style='width:100%;height:100%;object-fit:contain' src=''>"
            },
            {
                view: "toolbar",
                cols: [
                    startStopBtn,
                    {
                        view: "slider",
                        label: "Контраст",
                        value: 50,
                        min: 0,
                        max: 100,
                        on: { onChange: v => socket.emit("set_contrast", v) }
                    },
                    {
                        view: "slider",
                        label: "Яркость",
                        value: 50,
                        min: 0,
                        max: 100,
                        on: { onChange: v => socket.emit("set_brightness", v) }
                    }
                ]
            }
        ]
    };
}
