const $ = id => document.getElementById(id);
const fullExample = {
  "lyrics": "[Verse 1]\n晚风翻过安静的街巷\n路灯照着归途的方向\n我把白天没说完的话\n轻轻放进口袋里收藏\n\n[Chorus]\n让晚风带我回到你身旁\n把所有疲倦换成了月光\n不必急着回答明天怎样\n今夜我们就这样轻轻唱\n让晚风带我穿过这时光\n走过雨水也走过了晴朗\n只要心中还有一点希望\n最远的路也有温柔回响\n\n[Verse 2]\n地铁穿过城市的灯光\n窗外有人赶着去远方\n那些曾经走散的愿望\n仍在心里慢慢地生长\n\n[Chorus]\n让晚风带我回到你身旁\n把所有疲倦换成了月光\n不必急着回答明天怎样\n今夜我们就这样轻轻唱\n让晚风带我穿过这时光\n走过雨水也走过了晴朗\n只要心中还有一点希望\n最远的路也有温柔回响\n\n[Bridge]\n如果夜色暂时遮住方向\n就让星光落在你的肩上\n我们不必追赶每次散场\n慢一点也会抵达心中远方\n\n[Final Chorus]\n让晚风带我回到你身旁\n把所有疲倦换成了月光\n不必急着回答明天怎样\n今夜我们就这样轻轻唱\n让晚风带我穿过这时光\n走过雨水也走过了晴朗\n只要心中还有一点希望\n最远的路也有温柔回响",
  "style": "Mandarin Chinese, warm male baritone vocal, acoustic folk pop ballad, acoustic guitar, soft piano and restrained drums, 108 BPM, 4/4 meter, flowing verses and recurring chorus melody, brief instrumental introduction and natural resolved outro, clear diction",
  "target_seconds": 180,
  "seed": 42
};
const presets = {
  folk: "中文，温柔女声，原声吉他与轻柔钢琴，轻快民谣，100 BPM，自然清晰的演唱",
  jazz: "中文，温暖女声，钢琴、低音提琴和刷鼓，轻松爵士，98 BPM",
  rap: "中文说唱，清晰有节奏的男声，轻松的 hip-hop 鼓点和温暖贝斯，96 BPM",
  pop: "中文，明亮女声，流行音乐，钢琴、吉他与轻快鼓点，108 BPM，积极温暖"
};
const labels = {queued:"准备开始",running:"正在生成",completed:"生成完成",partial:"已生成片段 · 触及长度上限",failed:"生成失败",cancelled:"已取消",interrupted:"服务曾中断"};
let token = "", jobs = [], selected = null, busy = false, submitting = false;
function count(){ $("count").textContent = `${$("lyrics").value.length} / 4000`; }
function example(){
  $("lyrics").value=fullExample.lyrics; $("style").value=fullExample.style;
  $("mode").value="full"; $("duration").value=String(fullExample.target_seconds); $("seed").value=String(fullExample.seed);
  $("audio-budget").value="360"; $("score-budget").value="4096"; $("timeout").value="60"; count(); saveDraft();
}
const draftKey="yue2-studio-draft-v2";
const fields=["lyrics","style","mode","preset","seed","duration","audio-budget","score-budget","timeout"];
function saveDraft(){try{localStorage.setItem(draftKey,JSON.stringify(Object.fromEntries(fields.map(k=>[k,$(k).value]))));}catch{}}
function restoreDraft(){try{const d=JSON.parse(localStorage.getItem(draftKey)||"null");if(d)for(const k of fields)if(d[k]!==undefined)$(k).value=d[k];}catch{}}
$("example").onclick = example;
$("lyrics").oninput = () => {count();saveDraft();};
$("preset").onchange = () => { $("style").value = presets[$("preset").value]; if($("preset").value==="rap") $("mode").value="off"; else $("mode").value="full"; };
$("mode").onchange = saveDraft;
async function post(url,body){
  const r = await fetch(url,{method:"POST",headers:{"Content-Type":"application/json","X-Studio-Token":token},body:JSON.stringify(body)});
  const data = await r.json(); if(!r.ok) throw new Error(data.error||"请求失败，请刷新后重试。"); return data;
}
function render(){
  $("generate").disabled = busy || submitting || !token;
  $("generate").textContent = busy ? "本机已有生成任务" : "生成我的歌曲";
  const job = jobs.find(j=>j.id===selected) || jobs[0];
  $("empty").hidden = !!job; $("current").hidden = !job;
  if(job){
    selected=job.id;
    $("job-status").textContent=(job.status==="completed" && job.metrics?.duration_target_met===false)?"音频已生成 · 时长偏离目标":(labels[job.status]||job.status);
    $("job-title").textContent=job.request.lyrics.split("\n").filter(x=>!x.startsWith("["))[0] || "我的歌曲";
    const stage=job.stage||"";
    let text="正在加载本地模型";
    if(stage==="planned"||stage.startsWith("semantic:")) text="正在为歌词生成演唱与编配";
    if(stage.startsWith("abc:")) text="正在创作旋律与和弦曲谱";
    if(stage==="semantic_done") text="正在合成音频";
    if(stage==="synthesized") text="正在解码可播放音频";
    if(job.native_progress?.includes("Synthesizing audio")){
      const m=job.native_progress.match(/(\d+)\/(\d+) steps/);
      if(m) text=`正在合成音频 · ${m[1]} / ${m[2]} 步`;
    }
    if(job.native_progress?.includes("Decoding audio")) text="正在解码可播放音频";
    if(["completed","partial"].includes(job.status)) text=`实际 ${Math.floor(job.metrics.duration_seconds/60)} 分 ${(job.metrics.duration_seconds%60).toFixed(2)} 秒 · 48 kHz 双声道`;
    else if(["failed","cancelled","interrupted"].includes(job.status)) text="可以修改左侧输入，再生成一个新版本。";
    if(stage.startsWith("semantic:") && job.status==="running") text+=` · 已生成约 ${(Number(stage.split(":")[1])/25).toFixed(0)} 秒音乐序列`;
    $("stage").textContent=text;
    $("elapsed").textContent=`${job.finished_at?"本次用时":"已等待"} ${Math.floor(job.elapsed_seconds/60)} 分 ${job.elapsed_seconds%60} 秒${job.plan_nominal_seconds ? ` · 曲谱估计 ${job.plan_nominal_seconds.toFixed(1)} 秒（非成品时长）` : ""}`;
    $("cancel").hidden=!["queued","running"].includes(job.status);
    $("cancel").disabled=!!job.cancel_requested;
    $("cancel").textContent=job.cancel_requested?"正在停止…":"取消生成";
    $("job-error").textContent=job.error||"";
    $("saved-input").textContent=`${job.request.lyrics}\n\n风格：${job.request.style}\n随机种子：${job.request.seed}\n目标时长：${job.options?.target_seconds || "模型决定"} 秒\n音频预算：${job.options?.max_audio_seconds || 48} 秒`;
    $("result").hidden=!job.audio_url;
    if(job.audio_url){
      if($("player").dataset.job!==job.id){$("player").src=job.audio_url;$("player").dataset.job=job.id;}
      $("download").href=job.audio_url; $("download").download=`YuE2-${job.id}.flac`;
      $("score").hidden=!job.score_url; if(job.score_url){$("score").href=job.score_url;$("score").target="_blank";}
      $("quality").textContent=job.status==="partial"?`这次触及 ${job.options?.max_audio_seconds || 48} 秒生成预算，结尾可能不完整。请查看歌词与预算后重试。`:`音频已生成${job.metrics.duration_target_met===false ? "，实际长度偏离目标超过15秒" : ""}。连续生成不等于听感验收，请试听段落衔接、歌词、音色和结尾。`;
    }
  }
  $("history-empty").hidden=jobs.length>0;
  $("history-list").replaceChildren();
  for(const item of jobs){
    const btn=document.createElement("button");btn.className="history-item";btn.type="button";
    const title=document.createElement("span");title.textContent=(item.request.lyrics.split("\n").find(x=>x.trim() && !x.startsWith("[")) || "我的歌曲").slice(0,35);
    const duration=item.metrics?` · ${Math.floor(item.metrics.duration_seconds/60)}分${Math.floor(item.metrics.duration_seconds%60)}秒`:"";
    const status=document.createElement("small");status.textContent=`${labels[item.status]}${duration} · ${new Date(item.created_at*1000).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"})}`;
    btn.append(title,status);btn.onclick=()=>{selected=item.id;render();};$("history-list").append(btn);
  }
}
async function refresh(){
  try{
    const r=await fetch("/api/state",{cache:"no-store"});if(!r.ok)throw new Error();
    const data=await r.json();token=data.token;jobs=data.jobs;busy=!!data.active || !!data.external_busy;
    $("connection").textContent="本机服务在线";render();
  }catch{$("connection").textContent="连接中断，请确认本机服务已启动";token="";$("generate").disabled=true;}
}
$("song-form").onsubmit=async e=>{
  e.preventDefault();if(busy||submitting)return;
  $("form-error").textContent="";submitting=true;render();
  try{
    const data=await post("/api/jobs",{lyrics:$("lyrics").value,style:$("style").value,mode:$("mode").value,seed:Number($("seed").value),target_seconds:Number($("duration").value),max_audio_seconds:Number($("audio-budget").value),max_abc_tokens:Number($("score-budget").value),timeout_minutes:Number($("timeout").value)});
    selected=data.job.id;await refresh();
  }catch(error){$("form-error").textContent=error.message;}
  finally{submitting=false;render();}
};
$("cancel").onclick=async()=>{
  try{await post(`/api/jobs/${selected}/cancel`,{});await refresh();}
  catch(error){$("job-error").textContent=error.message;}
};
$("reuse").onclick=()=>{
  const job=jobs.find(j=>j.id===selected);if(!job)return;
  $("lyrics").value=job.request.lyrics;$("style").value=job.user_style || job.request.style;$("duration").value=String(job.options?.target_seconds || 0);$("audio-budget").value=job.options?.max_audio_seconds || 360;$("score-budget").value=job.options?.max_abc_tokens || 4096;$("timeout").value=job.options?.timeout_minutes || 60;$("mode").value=job.request.cot;$("seed").value=job.request.seed;count();saveDraft();$("lyrics").focus();window.scrollTo({top:0,behavior:"smooth"});
};
$("style").value=presets.folk;
restoreDraft();for(const k of fields)$(k).addEventListener("change",saveDraft);
count();refresh();setInterval(refresh,2500);
