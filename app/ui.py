from .config import DEMO_MODE, USDA_API_KEY

def build_ui_html() -> str:
    usda_enabled = "ENABLED" if USDA_API_KEY else "DISABLED"
    demo_on = "ON" if DEMO_MODE else "OFF"

    html = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>MealAnalyzer AI - Personal Nutritional Coach</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; max-width: 900px; margin: 36px auto; padding: 0 16px; }
    h2 { margin-bottom: 12px; }
    .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }
    .card { border: 1px solid #e5e5e5; border-radius: 10px; padding: 14px; background: #fff; }
    .card h3 { margin: 0 0 8px; font-size: 14px; color: #333; }
    label { font-size: 12px; color: #555; display:block; margin-bottom: 4px; }
    input, textarea, button { font-size: 14px; padding: 8px 10px; border-radius: 8px; border: 1px solid #ccc; }
    textarea { width: 100%; min-height: 90px; }
    button { cursor: pointer; }
    button.primary { background: #111; color: #fff; border-color: #111; }
    pre { background: #f7f7f7; padding: 12px; border-radius: 10px; overflow:auto; }
    .grid4 { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 10px; }
    .muted { color:#666; font-size: 12px; }
    .pill { display:inline-block; padding: 2px 8px; border-radius: 999px; background:#f1f1f1; font-size:12px; margin-left:6px; }
    .ok { color:#0a7; }
    .warn { color:#c70; }
    .banner { padding: 10px 12px; border-radius: 10px; margin-bottom: 10px; font-size: 13px; border: 1px solid #ddd; }
    .banner-green { background: #e9f7ef; border-color: #b7e3c6; color: #135b2b; }
    .banner-yellow { background: #fff7e6; border-color: #ffe2a8; color: #6a4a00; }
    .badge { display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid #ddd; font-size:12px; }
  </style>
</head>
<body>
  <h2>MealAnalyzer AI - Prem's Nutritional Coach</h2>
  <div class="muted">
    Voice → Speech-to-Text → Tool lookup (USDA/local) → Gemini reasoning → Firestore daily totals
    <span class="pill">Demo mode: __DEMO__</span>
    <span class="pill">USDA: __USDA__</span>
  </div>

  <div class="row" style="margin-top:14px;">
    <div class="card" style="flex: 1 1 520px;">
      <h3>Meal input</h3>
      <textarea id="msg">Please type or speak!</textarea>
      <div class="row" style="margin-top:10px;">
        <button onclick="recordVoice()">Record Voice (4s)</button>
        <button class="primary" onclick="analyze()">Analyze</button>
        <button onclick="resetMeals()">Reset Today's Meals</button>
      </div>
      <div class="muted" style="margin-top:8px;">
        Note: all estimates are conservative to support a sustainable fitness journey.
      </div>
    </div>

    <div class="card" style="flex: 1 1 320px;">
      <h3>Daily goals <span class="pill">High-protein lifter default</span></h3>
      <div class="grid4">
        <div><label>Calories</label><input id="g_cal" type="number" value="2200"/></div>
        <div><label>Protein (g)</label><input id="g_pro" type="number" value="180"/></div>
        <div><label>Carbs (g)</label><input id="g_carbs" type="number" value="220"/></div>
        <div><label>Fat (g)</label><input id="g_fat" type="number" value="70"/></div>
      </div>
      <div class="row" style="margin-top:10px;">
        <button onclick="saveGoals()">Save Goals</button>
        <span id="goalStatus" class="muted"></span>
      </div>
      <div class="muted" style="margin-top:8px;">
        Stored in Firestore (per-day doc). Re-run Analyze to use new goals.
      </div>
    </div>
  </div>

  <div class="row" style="margin-top:14px;">
    <div class="card" style="flex: 1 1 900px;">
      <h3>Results</h3>
      <div id="pretty"></div>
      <details style="margin-top:10px;">
        <summary class="muted">Show raw JSON</summary>
        <pre id="out"></pre>
      </details>
    </div>
  </div>

<script>
  function esc(s){ return (s ?? "").toString().replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;"); }

  function sourceBanner(toolUsed){
    const t = (toolUsed || "gemini").toLowerCase();
    if (t.includes("usda")) {
      return { cls: "banner banner-green", text: "Data source: USDA (verified lookup) + Gemini (reasoning)" };
    }
    if (t.includes("local")) {
      return { cls: "banner banner-yellow", text: "Data source: Local lookup (approx) + Gemini (reasoning)" };
    }
    return { cls: "banner banner-yellow", text: "Data source: Gemini approximation (no external lookup)" };
  }

  function renderPretty(data){
    if(data.error){
      document.getElementById("pretty").innerHTML =
        `<div class="warn"><b>Error:</b> ${esc(data.error)}</div>`;
      return;
    }

    const tool = data.tool_used || "gemini";
    const b = sourceBanner(tool);

    const ms = data.meal_summary || {};
    const ut = data.updated_totals || {};
    const rem = data.remaining || {};
    const adv = data.advice || {};

    document.getElementById("pretty").innerHTML = `
      <div class="${b.cls}">
        <b>${esc(b.text)}</b>
        <span class="badge" style="margin-left:8px;">tool_used: ${esc(tool)}</span>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
        <div class="card" style="border-color:#eee;">
          <h3>Meal</h3>
          <div><b>${esc(ms.description || "")}</b></div>
          <div class="muted">${esc(ms.assumptions || "")}</div>
          <div style="margin-top:8px;">
            Calories: <b>${esc(ms.calories)}</b><br/>
            Protein: <b>${esc(ms.protein_g)}g</b> | Carbs: <b>${esc(ms.carbs_g)}g</b> | Fat: <b>${esc(ms.fat_g)}g</b>
          </div>
        </div>

        <div class="card" style="border-color:#eee;">
          <h3>Remaining today</h3>
          <div>
            Calories: <b>${esc(rem.calories)}</b><br/>
            Protein: <b>${esc(rem.protein)}g</b> | Carbs: <b>${esc(rem.carbs)}g</b> | Fat: <b>${esc(rem.fat)}g</b>
          </div>
        </div>

        <div class="card" style="border-color:#eee;">
          <h3>Updated totals</h3>
          <div>
            Calories: <b>${esc(ut.calories)}</b><br/>
            Protein: <b>${esc(ut.protein)}g</b> | Carbs: <b>${esc(ut.carbs)}g</b> | Fat: <b>${esc(ut.fat)}g</b>
          </div>
        </div>

        <div class="card" style="border-color:#eee;">
          <h3>Advice</h3>
          <div>${esc(adv.protein_focus || "")}</div>
          <div class="muted" style="margin-top:6px;"><b>Next meal ideas:</b></div>
          <ul style="margin:6px 0 0 18px;">
            ${(adv.next_meal_ideas || []).map(x=>`<li>${esc(x)}</li>`).join("")}
          </ul>
          <div class="muted" style="margin-top:6px;"><b>Watch out:</b> ${esc(adv.watch_out_for || "")}</div>
        </div>
      </div>
    `;
  }

  async function analyze(txt=null){
    const res = await fetch("/chat",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        user_id:"demo",
        message: txt || document.getElementById("msg").value
      })
    });
    const data = await res.json();
    document.getElementById("out").innerText = JSON.stringify(data,null,2);
    renderPretty(data);
  }

  async function saveGoals(){
    const payload = {
      user_id: "demo",
      goals: {
        calories: Number(document.getElementById("g_cal").value),
        protein: Number(document.getElementById("g_pro").value),
        carbs: Number(document.getElementById("g_carbs").value),
        fat: Number(document.getElementById("g_fat").value),
      }
    };
    const res = await fetch("/set_goals",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    const el = document.getElementById("goalStatus");
    el.innerHTML = (data.status === "ok") ? '<span class="ok">Saved ✓</span>' : '<span class="warn">Save failed</span>';
  }

  async function resetMeals(){
    const res = await fetch("/reset_meals?user_id=demo",{ method:"POST" });
    const data = await res.json();
    document.getElementById("out").innerText = JSON.stringify(data,null,2);
    document.getElementById("pretty").innerHTML = "<div class='ok'><b>Meals reset for today.</b></div>";
  }

  async function recordVoice(){
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    const rec = new MediaRecorder(stream);
    let chunks=[];
    rec.ondataavailable=e=>chunks.push(e.data);
    rec.onstop=async()=>{
      const blob=new Blob(chunks,{type:"audio/webm"});
      const fd=new FormData();
      fd.append("file",blob);
      const r=await fetch("/transcribe",{method:"POST",body:fd});
      const d=await r.json();
      document.getElementById("msg").value=d.transcript || "";
      analyze(d.transcript || "");
    };
    rec.start();
    setTimeout(()=>rec.stop(),4000);
  }
</script>
</body>
</html>
"""
    html = html.replace("__DEMO__", demo_on).replace("__USDA__", usda_enabled)
    return html