// One media element survives every song change and catalog refresh.
// Each play promise belongs to a request; late failures must not affect its successor.
function createSongPlayer(media,{status,ended},clock={set:setTimeout,clear:clearTimeout}){
 let revision=0,wanted=false,retries=0,timer=null,resumeAt=0;
 const clear=()=>{if(timer!==null)clock.clear(timer);timer=null;};
 const report=(message,state)=>status(message,state);
 function failure(error,request){
  if(request!==revision||!wanted)return;
  media.dataset.lastPlaybackError=(error?.name||'MediaError')+': '+(error?.message||'');
  if(error?.name==='NotAllowedError'){
   clear();wanted=false;media.autoplay=false;
   report('浏览器暂停了自动播放。点击“继续播放”恢复当前歌曲。','blocked');return;
  }
  if(error?.name==='AbortError'&&!media.paused)return;
  if(timer!==null)return;
  if(retries<1){
   retries++;resumeAt=Number.isFinite(media.currentTime)?media.currentTime:0;
   report('音频加载中断，正在重新连接…','retrying');
   timer=clock.set(()=>{
    timer=null;if(request!==revision||!wanted)return;
    // Invalidate the rejected promise before load() aborts its previous resource.
    const next=++revision;media.load();attempt(next);
   },800);return;
  }
  clear();wanted=false;media.autoplay=false;
  const text=error?.name==='NotSupportedError'||media.error?.code===3||media.error?.code===4
   ?'音频读取或解码失败，可点“继续播放”重试，或切换下一首。'
   :'音频暂时未能加载，可点“继续播放”重试。';
  report(text,'failed');
 }
 function attempt(request){
  try{Promise.resolve(media.play()).catch(error=>failure(error,request));}
  catch(error){failure(error,request);}
 }
 media.addEventListener('loadedmetadata',()=>{
  if(resumeAt>0&&Number.isFinite(media.duration)){media.currentTime=Math.min(resumeAt,Math.max(0,media.duration-.05));resumeAt=0;}
 });
 media.addEventListener('playing',()=>{
  clear();wanted=true;media.autoplay=true;media.dataset.lastPlaybackError='';report('','playing');
 });
 media.addEventListener('pause',()=>{
  // load()/source changes may emit pause before their new play() takes effect.
  if(!media.paused||media.ended||media.error)return;
  wanted=false;media.autoplay=false;++revision;clear();report('已暂停','paused');
 });
 media.addEventListener('waiting',()=>{if(wanted)report('正在缓冲音频…','loading');});
 media.addEventListener('error',()=>{if(wanted)failure({name:'MediaError',message:media.error?.message||String(media.error?.code||'')},revision);});
 media.addEventListener('ended',()=>{clear();if(wanted)ended();});
 return {start(url){
  clear();const request=++revision;wanted=true;retries=0;resumeAt=0;
  // Set only after an explicit play action; keep the same element for the playlist.
  media.preload='auto';media.autoplay=true;
  report('正在加载音频…','loading');
  if(media.getAttribute('src')!==url)media.src=url;
  else if(media.error)media.load();
  else if(media.ended)media.currentTime=0;
  attempt(request);
 },stop(){++revision;wanted=false;clear();media.autoplay=false;media.pause();media.removeAttribute('src');media.load();report('','idle');}};
}

function songDownloadName(title,url){
 const name=(title||'未命名歌曲').replace(/[\\/:*?"<>|\x00-\x1f]/g,'-').trim().slice(0,100)||'未命名歌曲';
 const extension=url.split('?')[0].split('.').pop();
 return `小Rei-${name}.${extension}`;
}
'use strict';
function progressOf(job){
 if(job.progress)return job.progress;
 const stage=job.stage||'',native=job.native_progress||'',direct=job.request?.cot==='off';
 const score=direct?'曲谱规划已跳过（直接生成）':'曲谱已完成';
 let p={phase:'loading',text:'正在加载本地模型',completed:[],current:null,total:null};
 if(stage.startsWith('abc:')||native.includes('Planning score')){const m=native.match(/Planning score: (\d+) tokens/);p={...p,phase:'score',text:`正在创作曲谱 · 已生成 ${m?m[1]:stage.split(':')[1]||0} 个 token`,completed:['模型已加载']};}
 if(stage==='planned'||stage.startsWith('semantic:')||native.includes('Generating song')){const m=native.match(/Generating song: (\d+) tokens/);const n=Number(m?m[1]:stage.split(':')[1]||0);p={...p,phase:'semantic',text:`正在生成演唱与编配 · 已生成约 ${(n/25).toFixed(0)} 秒音乐序列（非成品时长）`,completed:[score]};}
 if(stage==='semantic_done'||native.includes('Synthesizing audio')){const m=native.match(/(\d+)\/(\d+) steps/);p={...p,phase:'synthesis',text:m?`正在合成音频 · ${m[1]} / ${m[2]} 步`:'正在合成音频',current:m?Number(m[1]):null,total:m?Number(m[2]):null,completed:[score,'演唱与编配序列已完成']};}
 if(stage==='synthesized'||native.includes('Decoding audio')||native.includes('Loading audio decoder')){const m=native.match(/(\d+)\/(\d+) chunks/);p={...p,phase:'decode',text:m?`正在解码音频 · ${m[1]} / ${m[2]} 块`:native.includes('Loading audio decoder')?'正在加载音频解码器':'正在解码音频',current:m?Number(m[1]):null,total:m?Number(m[2]):null,completed:[score,'音频合成已完成']};}
 if(stage==='complete')p={...p,phase:'save',text:'正在保存并校验原始音频',current:null,total:null,completed:[score,'音频合成与解码已完成']};
 if(['completed','partial'].includes(job.status))p={...p,phase:'done',text:(job.status==='partial'?'生成片段已保存':'歌曲已完成')+(job.audio_url?.endsWith('.mp3')?' · MP3 320 kbps':' · FLAC'),current:null,total:null,completed:['音频已生成']};
 if(job.status==='queued')p={...p,phase:'queued',text:'等待本机模型开始运行',current:null,total:null,completed:[]};
 if(['failed','cancelled','interrupted'].includes(job.status))p={...p,phase:job.status,text:{failed:'生成失败',cancelled:'已取消',interrupted:'生成已中断'}[job.status],current:null,total:null,completed:[]};
 return p;
}
function showStage(barId,noteId,p){const bar=$(barId);bar.hidden=['done','ready','failed','cancelled','interrupted','storage_failed'].includes(p.phase);bar.setAttribute('aria-label','当前阶段进度');if(p.total>0&&Number.isFinite(p.current)){bar.max=p.total;bar.value=p.current;}else{bar.removeAttribute('value');bar.removeAttribute('max');}$(noteId).textContent=(p.completed||[]).join(' · ')+(p.total>0?' · 进度条仅表示当前阶段':'');}

const $=id=>document.getElementById(id), audio=$('audio');
let library={tracks:[],batches:[],genres:{},voices:{}}, selected=null, playing=null, bag=[], queueKey='', playlist='all', deleting=null, deleteBusy=false;
const changing=new Set();let collectionVersion=0;
const statusNames={completed:'可播放',partial:'部分生成',failed:'生成失败',cancelled:'已取消',interrupted:'已中断',running:'正在生成',queued:'准备生成',pending:'等待生成'};
const duration=n=>Number.isFinite(n)?Math.floor(n/60)+':'+String(Math.floor(n%60)).padStart(2,'0'):'—';
function visible(){const q=$('search').value.trim().toLowerCase();return library.tracks.filter(t=>(playlist!=='liked'||t.favorite)&&(!$('batch').value||t.batch===$('batch').value)&&(!$('genre').value||t.genre===$('genre').value)&&(!$('voice').value||t.voice===$('voice').value)&&(!q||(t.title+' '+t.lyrics).toLowerCase().includes(q)));}
function playable(){return visible().filter(t=>t.audio_url);}
function subtitle(t){return [library.genres[t.genre]||'历史作品',library.voices[t.voice] ? library.voices[t.voice]+'目标' : '',t.batch].filter(Boolean).join(' / ');}
function render(){
 const rows=visible(), list=$('tracks');list.replaceChildren();
 for(const t of rows){const b=document.createElement('button');b.className='track'+(selected===t.id?' selected':'');b.dataset.genre=t.genre;b.dataset.id=t.id;b.setAttribute('aria-pressed',selected===t.id?'true':'false');
 const icon=document.createElement('span');icon.className='track-icon';icon.textContent=playing===t.id&&!audio.paused?'♫':t.audio_url?'▶':'·';
 const copy=document.createElement('span');copy.className='track-copy';const name=document.createElement('span');name.className='track-name';name.textContent=t.title;const sub=document.createElement('span');sub.className='track-sub';sub.textContent=subtitle(t);copy.append(name,sub);
 const end=document.createElement('span');end.className='track-end';end.textContent=t.audio_url?duration(t.duration)+(t.status==='partial'?' / 部分':''):t.progress?.text||statusNames[t.status]||t.status;b.append(icon,copy,end);b.onclick=()=>select(t.id,!!t.audio_url);
 const row=document.createElement('div');row.className='track-row'+(selected===t.id?' selected':'');row.dataset.trackId=t.id;row.append(b);
 if(t.audio_url){const actions=document.createElement('div');actions.className='track-actions';
 const heart=document.createElement('button');heart.className='heart'+(t.favorite?' liked':'');heart.textContent=t.favorite?'♥':'♡';heart.setAttribute('aria-label',(t.favorite?'取消喜欢':'喜欢')+'《'+t.title+'》');heart.setAttribute('aria-pressed',String(!!t.favorite));heart.title=t.favorite?'取消喜欢':'加入我喜欢的歌';heart.disabled=changing.has(t.id);heart.onclick=()=>toggleFavorite(t);actions.append(heart);
 const remove=document.createElement('button');remove.className='remove-song';remove.textContent='删除';remove.setAttribute('aria-label','删除《'+t.title+'》');remove.disabled=changing.has(t.id);remove.onclick=()=>askDelete(t);actions.append(remove);row.append(actions);}
 list.append(row);}
 const total=library.tracks.filter(t=>t.audio_url).length,liked=library.tracks.filter(t=>t.audio_url&&t.favorite).length;
 $('all-count').textContent=total;$('liked-count').textContent=liked;$('all-songs').setAttribute('aria-pressed',String(playlist==='all'));$('liked-songs').setAttribute('aria-pressed',String(playlist==='liked'));
 $('empty').textContent=playlist==='liked'?'还没有符合条件的喜欢歌曲。点击歌曲旁的空心爱心，把好歌留在这里。':'这里还没有符合条件的歌曲，试试其他筛选。';
 $('count').textContent=rows.filter(t=>t.audio_url).length+' 首可播放 / '+rows.length+' 条记录';$('empty').hidden=!!rows.length;
 const ids=playable().map(t=>t.id).join(',');if(ids!==queueKey){queueKey=ids;bag=[];}
 $('play-all').disabled=$('shuffle-all').disabled=!playable().length;
}
function notice(message){$('library-notice').textContent=message;}
async function mutateTrack(id,action,body){
 // Fetch the current service token: it changes after a service restart.
 const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(10000)});
 if(!response.ok)throw Error('无法连接本地服务，请稍后重试。');
 const state=await response.json();
 const result=await fetch('/api/library/tracks/'+encodeURIComponent(id)+'/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Studio-Token':state.token},body:JSON.stringify(body),signal:AbortSignal.timeout(30000)});
 const data=await result.json();if(!result.ok)throw Error(data.error||'操作未完成，请重试。');return data;
}
async function toggleFavorite(track){
 if(changing.has(track.id))return;changing.add(track.id);render();
 try{const result=await mutateTrack(track.id,'favorite',{favorite:!track.favorite});collectionVersion++;
 const current=library.tracks.find(t=>t.id===track.id);if(current)current.favorite=result.favorite;
 notice(result.favorite?'已将《'+track.title+'》加入我喜欢的歌。':'已取消喜欢《'+track.title+'》。');
 }catch(error){notice(error.message);}finally{changing.delete(track.id);render();}
}
function askDelete(track){deleting=track;$('delete-song-title').textContent='《'+track.title+'》';$('delete-error').textContent='';$('delete-dialog').showModal();$('cancel-delete').focus();}
function clearSelection(){selected=null;$('lyric-title').textContent='选一首歌';$('lyric-meta').textContent='点击歌曲，歌词会显示在这里。';$('lyrics').replaceChildren();$('song-details').hidden=true;}
function removeFromView(id){
 const wasPlaying=playing===id,wasListening=wasPlaying&&!audio.paused;
 const queue=playable(),index=queue.findIndex(t=>t.id===id);
 if(wasPlaying){playing=null;player.stop();$('now-title').textContent='还没有开始播放';$('now-meta').textContent='选择一首歌曲，开始听';}
 library.tracks=library.tracks.filter(t=>t.id!==id);bag=bag.filter(t=>t!==id);
 if(selected===id)clearSelection();render();
 const remaining=playable();
 if(wasPlaying&&remaining.length){const nextTrack=queue.slice(index+1).find(t=>remaining.some(r=>r.id===t.id))||remaining[0];select(nextTrack.id,wasListening);}
 else if(!selected&&remaining.length)select(remaining[0].id,false);
}
function reconcileSelection(){
 if(playing&&!library.tracks.some(t=>t.id===playing)){playing=null;player.stop();$('now-title').textContent='还没有开始播放';$('now-meta').textContent='选择一首歌曲，开始听';}
 if(selected&&!library.tracks.some(t=>t.id===selected))clearSelection();
}
async function confirmDelete(){
 if(!deleting||deleteBusy)return;deleteBusy=true;const track=deleting;
 $('confirm-delete').disabled=$('cancel-delete').disabled=true;$('confirm-delete').textContent='正在删除…';$('delete-error').textContent='';
 try{const result=await mutateTrack(track.id,'delete',{confirm_title:track.title});collectionVersion++;removeFromView(track.id);$('delete-dialog').close();deleting=null;
 notice('已永久删除《'+track.title+'》，清理约 '+(result.removed_bytes/1024/1024).toFixed(1)+' MB 文件。');refresh();
 }catch(error){$('delete-error').textContent=error.message;}
 finally{deleteBusy=false;$('confirm-delete').disabled=$('cancel-delete').disabled=false;$('confirm-delete').textContent='永久删除';}
}
$('confirm-delete').onclick=confirmDelete;$('cancel-delete').onclick=()=>{if(!deleteBusy){$('delete-dialog').close();deleting=null;}};
$('delete-dialog').addEventListener('cancel',event=>{if(deleteBusy)event.preventDefault();else deleting=null;});
function setPlaylist(value){playlist=value;bag=[];render();try{localStorage.setItem('midnight-playlist',value);}catch{}}
$('all-songs').onclick=()=>setPlaylist('all');$('liked-songs').onclick=()=>setPlaylist('liked');
function select(id,play=false){
 const t=library.tracks.find(t=>t.id===id);if(!t)return;selected=id;
 $('lyric-title').textContent=t.title;$('lyric-meta').textContent=subtitle(t)+(t.error?' · '+t.error:'');$('lyrics').replaceChildren();
 let section=null;for(const line of (t.lyrics||'这首历史样例未保存歌词。').split('\n')){if(!line.trim())continue;if(/^\[.*\]$/.test(line)||!section){section=document.createElement('section');section.className='verse';$('lyrics').append(section);if(/^\[.*\]$/.test(line)){const h=document.createElement('h3');h.textContent=line.slice(1,-1);section.append(h);continue;}}const p=document.createElement('p');p.textContent=line;section.append(p);}
 $('song-details').hidden=false;$('description').textContent=t.description||'';$('style').textContent=t.style||'';$('download').hidden=!t.audio_url;if(t.audio_url){$('download').href=t.download_url||t.audio_url;$('download').download=songDownloadName(t.title,t.audio_url);}
 if(play&&t.audio_url){
  playing=id;$('now-title').textContent=t.title;$('now-meta').textContent=subtitle(t);
  audio.loop=$('mode').value==='one';player.start(t.audio_url);
 }
 render();
}
function shuffled(ids){const out=[...ids];for(let i=out.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[out[i],out[j]]=[out[j],out[i]];}return out;}
function next(direction=1){const tracks=playable();if(!tracks.length)return;let id;if($('mode').value==='shuffle'){
 if(!bag.length)bag=shuffled(tracks.map(t=>t.id).filter(id=>id!==playing));id=bag.shift()||tracks[0].id;
 }else{const index=tracks.findIndex(t=>t.id===playing);id=tracks[(index<0?0:(index+direction+tracks.length)%tracks.length)].id;}select(id,true);}
const player=createSongPlayer(audio,{
 status:(message,state)=>{$('player-message').textContent=message;audio.dataset.playbackState=state;$('resume-playback').hidden=!['blocked','failed'].includes(state);render();},
 ended:()=>{if($('mode').value==='one'){audio.currentTime=0;player.start(audio.getAttribute('src'));}else next();}
});
$('resume-playback').onclick=()=>{const t=library.tracks.find(t=>t.id===playing);if(t)select(t.id,true);};
$('next').onclick=()=>next();$('previous').onclick=()=>next(-1);
$('mode').onchange=()=>{audio.loop=$('mode').value==='one';bag=[];try{localStorage.setItem('midnight-mode',$('mode').value);}catch{}};
$('play-all').onclick=()=>{const first=playable()[0];if(first)select(first.id,true);};
$('shuffle-all').onclick=()=>{$('mode').value='shuffle';$('mode').onchange();next();};
for(const id of ['batch','genre','voice'])$(id).onchange=render;$('search').oninput=render;
let refreshBusy=false,refreshTimer=null;
async function refresh(){if(refreshBusy)return;refreshBusy=true;clearTimeout(refreshTimer);try{const version=collectionVersion;const r=await fetch('/api/library',{signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error(r.status);const incoming=await r.json();if(version!==collectionVersion)return;library=incoming;reconcileSelection();if(library.tracks.some(t=>t.status==='running'&&!t.progress)){const stateResponse=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(10000)});if(stateResponse.ok){const state=await stateResponse.json();const live=new Map(state.jobs.map(j=>[j.id,j]));for(const t of library.tracks){const j=live.get(t.id);if(j&&!t.progress)t.progress=progressOf(j);}}}library.tracks=library.tracks.filter(t=>!(['completed','partial','cancelled'].includes(t.status)&&!t.audio_url));const current=$('batch').value;$('batch').replaceChildren(new Option('所有日期',''));for(const batch of [...new Set(library.tracks.map(t=>t.batch))].sort().reverse())$('batch').add(new Option(batch,batch));$('batch').value=current;
 const newest=library.batches[0];const names={running:'正在生成',waiting:'等待前一批完成',completed:'已完成',partial:'部分完成',failed:'遇到问题',prepared:'歌词已就绪'};$('batch-status').textContent=newest?`${newest.id}：${newest.completed} / ${newest.total} 首完成，${names[newest.status]||newest.status}${newest.removed?'，已永久删除 '+newest.removed+' 首':''}。`:'历史歌曲已载入。';const active=library.tracks.find(t=>t.status==='running'||['mp3','verify','cleanup'].includes(t.progress?.phase));$('generation-progress').hidden=!active;if(active){const p=active.progress||progressOf(active);$('generation-title').textContent='正在制作 · '+active.title;$('generation-stage').textContent=p.text;showStage('generation-bar','generation-completed',p);}render();
 if(!selected&&visible().length)select((playable()[0]||visible()[0]).id);
 }catch{$('batch-status').textContent='暂时无法连接本地服务，正在播放的歌曲不受刷新影响。';}finally{refreshBusy=false;refreshTimer=setTimeout(refresh,document.hidden?15000:3000);}}
$('refresh').onclick=refresh;try{const savedPlaylist=localStorage.getItem('midnight-playlist');if(savedPlaylist==='liked')playlist='liked';const saved=localStorage.getItem('midnight-mode');if(['list','one','shuffle'].includes(saved))$('mode').value=saved;}catch{}
refresh();document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
if('mediaSession' in navigator){navigator.mediaSession.setActionHandler('play',()=>{const t=library.tracks.find(t=>t.id===playing);if(t)select(t.id,true);});navigator.mediaSession.setActionHandler('pause',()=>audio.pause());navigator.mediaSession.setActionHandler('nexttrack',()=>next());navigator.mediaSession.setActionHandler('previoustrack',()=>next(-1));}
