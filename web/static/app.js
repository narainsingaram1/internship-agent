// Minimalist Web UI Application Logic

let ws = null;
let currentProfile = {};

document.addEventListener("DOMContentLoaded", () => {
  if (window.lucide) lucide.createIcons();
  initWebSocket();
  loadStatus();
  loadProfile();
});

// Initialize WebSocket for real-time logs and state
function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log("Connected to AutoApply WebSocket stream.");
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleServerEvent(data);
  };

  ws.onclose = () => {
    setTimeout(initWebSocket, 2000);
  };
}

function handleServerEvent(data) {
  if (data.type === "log") {
    appendLog(data.timestamp, data.message, data.level);
  } else if (data.type === "step") {
    setStep(data.step);
  } else if (data.type === "review_ready") {
    showReviewPanel(data);
  } else if (data.type === "completed") {
    handleCompleted(data.status);
  } else if (data.type === "error") {
    appendLog(new Date().toLocaleTimeString(), `Error: ${data.message}`, "error");
    document.getElementById("startBtn").disabled = false;
  }
}

function appendLog(time, msg, level = "info") {
  const consoleEl = document.getElementById("logConsole");
  const row = document.createElement("div");
  row.className = "flex items-start space-x-2";

  let color = "text-zinc-400";
  if (level === "error") color = "text-red-400 font-semibold";
  if (msg.includes("Role:") || msg.includes("Autofill complete")) color = "text-emerald-400 font-semibold";

  row.innerHTML = `<span class="text-zinc-600 select-none">[${time}]</span> <span class="${color}">${msg}</span>`;
  consoleEl.appendChild(row);
  consoleEl.scrollTop = consoleEl.scrollHeight;

  document.getElementById("terminalSection").classList.remove("hidden");
}

function clearLogs() {
  document.getElementById("logConsole").innerHTML = "";
}

function setStep(stepNum) {
  document.getElementById("stepperSection").classList.remove("hidden");
  for (let i = 1; i <= 5; i++) {
    const el = document.getElementById(`step-${i}`);
    if (!el) continue;
    if (i < stepNum) {
      el.className = "step-card done";
    } else if (i === stepNum) {
      el.className = "step-card active";
    } else {
      el.className = "step-card";
    }
  }
}

async function loadStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    if (data.model) {
      document.getElementById("modelBadge").innerText = data.model;
    }
  } catch (e) {
    console.error(e);
  }
}

// Start Autofill Run
async function startApplication() {
  const url = document.getElementById("jobUrlInput").value.trim();
  const headed = document.getElementById("headedCheckbox").checked;

  if (!url || !url.startsWith("http")) {
    alert("Please enter a valid URL starting with http:// or https://");
    return;
  }

  const btn = document.getElementById("startBtn");
  btn.disabled = true;
  btn.innerHTML = `<span class="inline-block animate-spin mr-1">↻</span> Launching...`;

  document.getElementById("reviewSection").classList.add("hidden");
  clearLogs();
  setStep(1);

  try {
    const res = await fetch("/api/apply/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url, headed: headed }),
    });
    if (!res.ok) throw new Error("Could not start application engine");
  } catch (err) {
    alert(err.message);
    btn.disabled = false;
    btn.innerHTML = `Launch Agent`;
  }
}

// Show HITL Review Screen
function showReviewPanel(data) {
  const btn = document.getElementById("startBtn");
  btn.disabled = false;
  btn.innerHTML = `<i data-lucide="sparkles" class="w-4 h-4"></i><span>Launch Agent</span>`;
  if (window.lucide) lucide.createIcons();

  const meta = data.job_meta || {};
  document.getElementById("reviewPlatform").innerText = (meta.platform || "GENERIC").toUpperCase();
  document.getElementById("reviewTitle").innerText = meta.title || "Job Application";
  document.getElementById("reviewCompany").innerText = meta.company || "Company";

  // Fields Table
  const tableBody = document.getElementById("fieldsTableBody");
  tableBody.innerHTML = "";
  const succeeded = data.results?.succeeded || [];
  document.getElementById("fieldsCount").innerText = succeeded.length;

  succeeded.forEach((item) => {
    const tr = document.createElement("tr");
    tr.className = "hover:bg-zinc-850/50 transition";
    tr.innerHTML = `
      <td class="py-2.5 font-medium text-white">${item.field}</td>
      <td class="py-2.5 text-zinc-300 font-mono text-[11px] truncate max-w-[200px]" title="${item.value}">${item.value}</td>
      <td class="py-2.5 text-right font-mono text-emerald-400">${Math.round((item.confidence || 1.0) * 100)}%</td>
    `;
    tableBody.appendChild(tr);
  });

  // Flagged questions
  const questionsCard = document.getElementById("questionsReviewCard");
  const questionsList = document.getElementById("questionsList");
  questionsList.innerHTML = "";
  const reviewQuestions = data.plan?.questions_to_review || [];

  if (reviewQuestions.length > 0) {
    reviewQuestions.forEach((q) => {
      const li = document.createElement("li");
      li.innerText = q;
      questionsList.appendChild(li);
    });
    questionsCard.classList.remove("hidden");
  } else {
    questionsCard.classList.add("hidden");
  }

  // Refresh screenshot
  const screenshotImg = document.getElementById("screenshotImg");
  screenshotImg.src = `/api/screenshot?t=${new Date().getTime()}`;

  document.getElementById("reviewSection").classList.remove("hidden");
  document.getElementById("reviewSection").scrollIntoView({ behavior: "smooth" });
}

// Submit Application
async function submitApplication() {
  if (!confirm("Confirm submission: This will click the final submit button on the live form. Proceed?")) return;

  try {
    const res = await fetch("/api/apply/submit", { method: "POST" });
    if (!res.ok) throw new Error("Submission failed");
    alert("🎉 Application submitted successfully!");
    document.getElementById("reviewSection").classList.add("hidden");
  } catch (err) {
    alert("Error submitting: " + err.message);
  }
}

// Abort Application
async function abortApplication() {
  if (!confirm("Are you sure you want to discard and close this session?")) return;

  try {
    await fetch("/api/apply/abort", { method: "POST" });
    document.getElementById("reviewSection").classList.add("hidden");
    appendLog(new Date().toLocaleTimeString(), "Session aborted by user.", "error");
  } catch (err) {
    console.error(err);
  }
}

function handleCompleted(status) {
  const btn = document.getElementById("startBtn");
  btn.disabled = false;
  btn.innerHTML = `<i data-lucide="sparkles" class="w-4 h-4"></i><span>Launch Agent</span>`;
  if (window.lucide) lucide.createIcons();
}

// Modal management
function openModal(id) {
  document.getElementById(id).classList.remove("hidden");
  if (id === "historyModal") loadHistory();
}

function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
}

// Profile management
async function loadProfile() {
  try {
    const res = await fetch("/api/profile");
    currentProfile = await res.json();

    const p = currentProfile.personal || {};
    const e = currentProfile.education || {};
    const l = currentProfile.links || {};

    document.getElementById("profFirstName").value = p.first_name || "";
    document.getElementById("profLastName").value = p.last_name || "";
    document.getElementById("profEmail").value = p.email || "";
    document.getElementById("profPhone").value = p.phone || "";
    document.getElementById("profSchool").value = e.school || "";
    document.getElementById("profMajor").value = e.major || "";
    document.getElementById("profGpa").value = e.gpa || "";
    document.getElementById("profLinkedin").value = l.linkedin || "";
    document.getElementById("profGithub").value = l.github || "";
    document.getElementById("profPortfolio").value = l.portfolio || "";
  } catch (e) {
    console.error("Failed to load profile:", e);
  }
}

async function saveProfile() {
  currentProfile.personal = currentProfile.personal || {};
  currentProfile.education = currentProfile.education || {};
  currentProfile.links = currentProfile.links || {};

  currentProfile.personal.first_name = document.getElementById("profFirstName").value.trim();
  currentProfile.personal.last_name = document.getElementById("profLastName").value.trim();
  currentProfile.personal.email = document.getElementById("profEmail").value.trim();
  currentProfile.personal.phone = document.getElementById("profPhone").value.trim();

  currentProfile.education.school = document.getElementById("profSchool").value.trim();
  currentProfile.education.major = document.getElementById("profMajor").value.trim();
  currentProfile.education.gpa = document.getElementById("profGpa").value.trim();

  currentProfile.links.linkedin = document.getElementById("profLinkedin").value.trim();
  currentProfile.links.github = document.getElementById("profGithub").value.trim();
  currentProfile.links.portfolio = document.getElementById("profPortfolio").value.trim();

  // Handle resume upload if chosen
  const resumeInput = document.getElementById("resumeFileInput");
  if (resumeInput.files.length > 0) {
    const formData = new FormData();
    formData.append("file", resumeInput.files[0]);
    await fetch("/api/upload-resume", { method: "POST", body: formData });
  }

  await fetch("/api/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(currentProfile),
  });

  alert("✅ Profile saved!");
  closeModal("profileModal");
}

async function loadHistory() {
  const container = document.getElementById("historyList");
  container.innerHTML = `<p class="text-zinc-500">Loading history...</p>`;

  try {
    const res = await fetch("/api/history");
    const apps = await res.json();

    if (!apps || apps.length === 0) {
      container.innerHTML = `<p class="text-zinc-500">No applications recorded yet.</p>`;
      return;
    }

    container.innerHTML = "";
    apps.reverse().forEach((app) => {
      const card = document.createElement("div");
      card.className = "bg-zinc-800/50 border border-zinc-800 rounded-xl p-3.5 space-y-1.5";
      const statusColor = app.status === "Submitted" ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/20" : "text-zinc-400 bg-zinc-800 border-zinc-700";

      card.innerHTML = `
        <div class="flex items-center justify-between">
          <span class="font-semibold text-white">${app.title || "Unknown Role"}</span>
          <span class="text-[10px] font-mono px-2 py-0.5 rounded border ${statusColor}">${app.status}</span>
        </div>
        <div class="text-zinc-400 text-xs">${app.company || "Company"}</div>
        <div class="flex items-center justify-between pt-1 text-[11px] text-zinc-500 font-mono">
          <span>${new Date(app.timestamp).toLocaleDateString()}</span>
          <a href="${app.url}" target="_blank" class="text-cyan-400 hover:underline">Job Link ↗</a>
        </div>
      `;
      container.appendChild(card);
    });
  } catch (err) {
    container.innerHTML = `<p class="text-red-400">Error loading history.</p>`;
  }
}
