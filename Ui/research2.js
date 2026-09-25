/* =====================================================
   NOVA RESEARCH MODE ENGINE — wired to the backend
   Polls /api/research/status while the Python pipeline
   (actions/research.py) does the fetching + PDF work.
===================================================== */


/* =====================================================
   CLOCK
===================================================== */

const timeElement =
    document.getElementById("time");


function updateClock() {

    const now = new Date();

    const hours =
        String(now.getHours()).padStart(2, "0");

    const minutes =
        String(now.getMinutes()).padStart(2, "0");

    const seconds =
        String(now.getSeconds()).padStart(2, "0");

    timeElement.textContent =
        `${hours}:${minutes}:${seconds}`;

}


setInterval(updateClock, 1000);

updateClock();


/* =====================================================
   BACKEND HELPERS
===================================================== */

async function api(path, body) {

    const options = body
        ? {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
          }
        : undefined;

    return (await fetch(path, options)).json();

}


/* =====================================================
   ELEMENTS
===================================================== */

const topicInput =
    document.getElementById("topicInput");

const startButton =
    document.getElementById("startResearch");

const micButton =
    document.getElementById("micButton");

const commandStatus =
    document.getElementById("commandStatus");

const systemStatus =
    document.getElementById("systemStatus");

const systemDescription =
    document.getElementById("systemDescription");

const pctValue =
    document.getElementById("pctValue");

const pctBar =
    document.getElementById("pctBar");

const pctDetail =
    document.getElementById("pctDetail");

const coreState =
    document.getElementById("coreState");

const stepCount =
    document.getElementById("stepCount");

const srcCount =
    document.getElementById("srcCount");

const sourceList =
    document.getElementById("sourceList");

const srcWiki =
    document.getElementById("srcWiki");

const srcRelated =
    document.getElementById("srcRelated");

const srcWeb =
    document.getElementById("srcWeb");

const stepEls = {};

document.querySelectorAll("#stepList .step").forEach(el => {
    stepEls[el.dataset.step] = el;
});

const STEP_ORDER =
    ["plan", "wikipedia", "related", "web", "summary", "pdf"];


/* =====================================================
   STATUS RENDERING
===================================================== */

function setSystem(title, detail) {

    systemStatus.textContent = title;

    systemDescription.textContent = detail;

}


function markSteps(currentStep, done) {

    const idx = STEP_ORDER.indexOf(currentStep);

    STEP_ORDER.forEach((name, i) => {

        const el = stepEls[name];

        if (!el) return;

        el.classList.remove("active", "done", "error");

        if (done) {

            el.classList.add(i < idx ? "done" : "active");

        } else if (i < idx) {

            el.classList.add("done");

        } else if (i === idx) {

            el.classList.add("active");

        }

    });

    /* "4/6" style counter follows the same frontier */
    const reached = done ? idx : idx + 1;

    stepCount.textContent =
        `${Math.max(0, reached)}/${STEP_ORDER.length}`;

}


function renderStatus(s) {

    pctValue.textContent = Math.round(s.pct || 0);

    pctBar.style.width = Math.min(100, s.pct || 0) + "%";

    pctDetail.textContent =
        (s.detail || "").toUpperCase().slice(0, 40) || "AWAITING TOPIC";

    if (s.error) {

        coreState.textContent = "RESEARCH FAILED";

        setSystem("ERROR", s.error.slice(0, 60));

        markSteps(s.step, false);

        stepEls[s.step] && stepEls[s.step].classList.add("error");

        commandStatus.textContent = "ERROR";

        startButton.disabled = false;

        return;

    }

    if (s.running) {

        coreState.textContent =
            `RESEARCHING — ${s.step.toUpperCase()}`;

        setSystem("RESEARCHING", s.detail || "Working...");

        markSteps(s.step, false);

        commandStatus.textContent = "BUSY";

        startButton.disabled = true;

        return;

    }

    startButton.disabled = false;

    commandStatus.textContent = "READY";

    if (s.done && s.pdf_path) {

        coreState.textContent = "PDF ON DESKTOP";

        setSystem("COMPLETE", "Research delivered, sir");

        STEP_ORDER.forEach(name => {

            stepEls[name] && stepEls[name].classList.add("done");

        });

        pctValue.textContent = "100";

        pctBar.style.width = "100%";

        pctDetail.textContent =
            "SAVED: " + fileNameOf(s.pdf_path).toUpperCase().slice(0, 40);

        return;

    }

    /* idle */
    coreState.textContent = "AWAITING TOPIC";

    setSystem("ACTIVE", "Research systems operational");

    markSteps("plan", false);

    STEP_ORDER.forEach(name => {

        stepEls[name] && stepEls[name].classList.remove("active", "done");

    });

    pctValue.textContent = "0";

    pctBar.style.width = "0%";

    pctDetail.textContent = "AWAITING TOPIC";

}


function fileNameOf(p) {

    return (p || "").split(/[\\/]/).pop();

}


/* =====================================================
   SOURCE LIST
===================================================== */

function renderSources(sources) {

    srcCount.textContent = (sources || []).length;

    if (!sources || !sources.length) {

        sourceList.innerHTML =
            '<div><span>--:--</span>NONE YET</div>';

        srcWeb.textContent = "READY";

        return;

    }

    srcWeb.textContent = `${sources.length} FOUND`;

    sourceList.innerHTML = "";

    sources.forEach((s, i) => {

        const row = document.createElement("div");

        const n = document.createElement("span");

        n.textContent =
            String(i + 1).padStart(2, "0");

        row.appendChild(n);

        row.appendChild(
            document.createTextNode(
                (s.title || s.url || "").slice(0, 48).toUpperCase()
            )
        );

        sourceList.appendChild(row);

    });

}


/* =====================================================
   START A RESEARCH RUN
===================================================== */

async function startResearch() {

    const topic = topicInput.value.trim();

    if (!topic) {

        topicInput.focus();

        return;

    }

    try {

        const r = await api("/api/research", { topic: topic });

        if (!r.started) {

            commandStatus.textContent = "BUSY";

            systemDescription.textContent =
                r.detail || r.error || "A run is already in progress, sir.";

            return;

        }

        topicInput.value = "";

        commandStatus.textContent = "BUSY";

        setSystem("RESEARCHING", "The pipeline is running...");

        if (window.NovaGlobe) {

            NovaGlobe.setPhrase(topic);

            NovaGlobe.pulse();

        }

    } catch (error) {

        commandStatus.textContent = "OFFLINE";

        setSystem("ERROR", "Backend unreachable, sir");

    }

}


startButton.addEventListener(
    "click",
    startResearch
);


topicInput.addEventListener(
    "keydown",
    event => {

        if (event.key === "Enter") {

            startResearch();

        }

    }
);


/* =====================================================
   LIVE STATUS POLLING
===================================================== */

let lastErrorShown = null;

async function poll() {

    try {

        const s = await api("/api/research/status");

        renderStatus(s);

        renderSources(s.sources);

        /* per-stage indicators on the left card */
        const order = STEP_ORDER.indexOf(s.step);

        srcWiki.textContent =
            s.step === "plan" ? "READY"
            : order >= 1 ? "SCANNED" : "...";

        srcRelated.textContent =
            s.step === "plan" || s.step === "wikipedia" ? "READY"
            : order >= 2 ? "SCANNED" : "...";

        if (s.error) {

            lastErrorShown = s.error;

        }

        /* a finished run speaks through NOVA's voice — once */
        if (s.done && s.pdf_path && !window._novaAnnounced) {

            window._novaAnnounced = true;

            await api("/api/announce", {
                text: `Research on ${s.topic} is complete, sir — the PDF is on your desktop.`
            });

        }

    } catch (error) {

        /* backend offline — stay calm */

    }

}


setInterval(poll, 1500);

poll();


/* =====================================================
   VOICE INPUT — speak the topic
===================================================== */

let listening = false;

async function listenOnce() {

    if (listening) {

        return;

    }

    listening = true;

    micButton.classList.add("listening");

    commandStatus.textContent = "LISTEN";

    try {

        const data = await api("/api/listen", {});

        if (data.transcript) {

            topicInput.value = data.transcript;

        } else if (data.reply) {

            commandStatus.textContent = "MUTED?";

        }

    } catch (error) {

        commandStatus.textContent = "MIC ERROR";

    }

    micButton.classList.remove("listening");

    commandStatus.textContent = "READY";

    listening = false;

}


micButton.addEventListener(
    "click",
    listenOnce
);


/* =====================================================
   EXIT — ESC or the footer link
===================================================== */

async function exitResearch() {

    if (document.body.classList.contains("stand-down")) {

        return;

    }

    document.body.classList.add("stand-down");

    playExitSound();

    setTimeout(() => {

        location.href = "/";

    }, 420);

}


document.addEventListener(
    "keydown",
    event => {

        if (event.key === "Escape") {

            exitResearch();

        }

    }
);


const standbyLink =
    document.getElementById("standbyLink");

standbyLink.addEventListener(
    "click",
    event => {

        event.preventDefault();

        exitResearch();

    }
);


/* -----------------------------------------
   EXIT SOUND — descending chime, normal mode
----------------------------------------- */

let _audioCtx = null;

function audioCtx() {

    if (!_audioCtx) {

        _audioCtx =
            new (window.AudioContext || window.webkitAudioContext)();

    }

    if (_audioCtx.state === "suspended") {

        _audioCtx.resume();

    }

    return _audioCtx;

}


function playExitSound() {

    try {

        const ctx = audioCtx();

        const now = ctx.currentTime;

        const sweep = ctx.createOscillator();

        const sweepGain = ctx.createGain();

        sweep.type = "sine";

        sweep.frequency.setValueAtTime(660, now);

        sweep.frequency.exponentialRampToValueAtTime(220, now + 0.4);

        sweepGain.gain.setValueAtTime(0.0001, now);

        sweepGain.gain.exponentialRampToValueAtTime(0.15, now + 0.08);

        sweepGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.45);

        sweep.connect(sweepGain).connect(ctx.destination);

        sweep.start(now);

        sweep.stop(now + 0.5);

    } catch (error) {

        /* audio unavailable — exit proceeds silently */

    }

}


/* =====================================================
   ENTRANCE — brief blue boot flash
===================================================== */

window.addEventListener("load", () => {

    document.body.classList.add("research-enter");

    setTimeout(
        () => document.body.classList.remove("research-enter"),
        700
    );

    /* pick up a run that's already in progress */
    poll();

});


/* =====================================================
   LIVE NEURAL GLOBE — same classifier, blue theme
===================================================== */

(function () {

    const el = document.getElementById("research-globe");

    if (!el || !window.NovaGlobe) {

        return;

    }

    if (!NovaGlobe.init(el, "blue")) {

        return;

    }

    topicInput.addEventListener("input", () => {

        NovaGlobe.setPhrase(topicInput.value);

    });

})();
