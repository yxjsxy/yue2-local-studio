const $ = id => document.getElementById(id);
const presets = {
  folk: "中文，温柔女声，原声吉他与轻柔钢琴，轻快民谣，100 BPM，自然清晰的演唱",
  jazz: "中文，温暖女声，钢琴、低音提琴和刷鼓，轻松爵士，98 BPM",
  rap: "中文说唱，清晰有节奏的男声，轻松的 hip-hop 鼓点和温暖贝斯，96 BPM",
  pop: "中文，明亮女声，流行音乐，钢琴、吉他与轻快鼓点，108 BPM，积极温暖"
};
const labels = {queued:"准备开始",running:"正在生成",completed:"生成完成",partial:"已生成片段 · 触及长度上限",failed:"生成失败",cancelled:"已取消",interrupted:"服务曾中断"};
let token = "", jobs = [], selected = null, busy = false, submitting = false;
function count(){ $("count").textContent = `${$("lyrics").value.length} / 160`; }
function example(){
  $("lyrics").value = "窗边落下一束光\n咖啡微热书页香\n慢慢走过小巷旁\n把好心情轻轻唱";
  $("style").value = presets.folk; $("preset").value = "folk"; $("mode").value = "full"; count();
}
$("example").onclick = example;
$("lyrics").oninput = count;
$("preset").onchange = () => { $("style").value = presets[$("preset").value]; if($("preset").value==="rap") $("mode").value="off"; else $("mode").value="full"; };
$("mode").onchange = () => {if($("mode").value==="off"){ $("preset").value="rap"; $("style").value=presets.rap; }};
async function post(url,body){
  const r = await fetch(url,{method:"POST",headers:{"Content-Type":"application/json","X-Studio-Token":token},body:JSON.stringify(body)});
  const data = await r.json(); if(!r.ok) throw new Error(data.error||"请求失败，请刷新后重试。"); return data;
}
function render(){
  $("generate").disabled = busy || submitting || !token;
  $("generate").textContent = busy ? "已有歌曲正在生成" : "生成我的歌曲";
  const job = jobs.find(j=>j.id===selected) || jobs[0];
  $("empty").hidden = !!job; $("current").hidden = !job;
  if(job){
    selected=job.id;
    $("job-status").textContent=labels[job.status]||job.status;
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
    if(["completed","partial"].includes(job.status)) text=`${job.metrics.duration_seconds.toFixed(2)} 秒 · 48 kHz 双声道`;
    else if(["failed","cancelled","interrupted"].includes(job.status)) text="可以修改左侧输入，再生成一个新版本。";
    $("stage").textContent=text;
    $("elapsed").textContent=`${job.finished_at?"本次用时":"已等待"} ${Math.floor(job.elapsed_seconds/60)} 分 ${job.elapsed_seconds%60} 秒`;
    $("cancel").hidden=!["queued","running"].includes(job.status);
    $("cancel").disabled=!!job.cancel_requested;
    $("cancel").textContent=job.cancel_requested?"正在停止…":"取消生成";
    $("job-error").textContent=job.error||"";
    $("saved-input").textContent=`${job.request.lyrics}\n\n风格：${job.request.style}\n随机种子：${job.request.seed}`;
    $("result").hidden=!job.audio_url;
    if(job.audio_url){
      if($("player").dataset.job!==job.id){$("player").src=job.audio_url;$("player").dataset.job=job.id;}
      $("download").href=job.audio_url; $("download").download=`YuE2-${job.id}.flac`;
      $("score").hidden=!job.score_url; if(job.score_url){$("score").href=job.score_url;$("score").target="_blank";}
      $("quality").textContent=job.status==="partial"?"这次触及约48秒的长度上限，结尾可能不完整。可减少歌词后重试。":"音频已生成。请试听歌词、咬字和风格是否符合你的预期。";
    }
  }
  $("history-empty").hidden=jobs.length>0;
  $("history-list").replaceChildren();
  for(const item of jobs){
    const btn=document.createElement("button");btn.className="history-item";btn.type="button";
    const title=document.createElement("span");title.textContent=item.request.lyrics.split("\n")[0].slice(0,35);
    const status=document.createElement("small");status.textContent=`${labels[item.status]} · ${new Date(item.created_at*1000).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"})}`;
    btn.append(title,status);btn.onclick=()=>{selected=item.id;render();};$("history-list").append(btn);
  }
}
async function refresh(){
  try{
    const r=await fetch("/api/state",{cache:"no-store"});if(!r.ok)throw new Error();
    const data=await r.json();token=data.token;jobs=data.jobs;busy=!!data.active;
    $("connection").textContent="本机服务在线";render();
  }catch{$("connection").textContent="连接中断，请确认本机服务已启动";token="";$("generate").disabled=true;}
}
$("song-form").onsubmit=async e=>{
  e.preventDefault();if(busy||submitting)return;
  $("form-error").textContent="";submitting=true;render();
  try{
    const data=await post("/api/jobs",{lyrics:$("lyrics").value,style:$("style").value,mode:$("mode").value,seed:Number($("seed").value)});
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
  $("lyrics").value=job.request.lyrics;$("style").value=job.request.style;$("mode").value=job.request.cot;$("seed").value=job.request.seed;count();$("lyrics").focus();window.scrollTo({top:0,behavior:"smooth"});
};
$("style").value=presets.folk;
count();refresh();setInterval(refresh,2500);
