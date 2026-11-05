const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('studio-ui/library.js','utf8');
const behavior=source.slice(source.indexOf('function removeFromView('),source.indexOf("$('confirm-delete').onclick"));
function page(paused=false){
 const a={id:'a',title:'正在听的歌',audio_url:'/a.mp3'},b={id:'b',title:'下一首',audio_url:'/b.mp3'},elements=new Map(),calls=[];
 const context={library:{tracks:[a,b]},playing:'a',selected:'a',bag:['a','b'],audio:{paused},deleting:a,deleteBusy:false,collectionVersion:0,
  $:id=>{if(!elements.has(id))elements.set(id,{textContent:'',disabled:false,close(){calls.push(['close']);}});return elements.get(id);},
  playable(){return context.library.tracks;},player:{stop(){calls.push(['stop']);}},clearSelection(){context.selected=null;},render(){},
  select(id,play){context.selected=id;if(play)context.playing=id;calls.push(['select',id,play]);},
  async mutateTrack(id,action,body){calls.push(['request',id,action,body]);return {removed_bytes:1024};},notice(message){calls.push(['notice',message]);},refresh(){calls.push(['refresh']);}};
 vm.createContext(context);vm.runInContext(behavior,context);return {context,calls,elements};
}
test('deleting current track stops source and continues with the remaining queue',async()=>{const {context:c,calls}=page();await c.confirmDelete();assert.deepEqual(c.library.tracks.map(t=>t.id),['b']);assert.equal(c.playing,'b');assert.equal(c.selected,'b');assert.equal(c.deleteBusy,false);assert.ok(calls.some(x=>x[0]==='stop'));assert.ok(calls.some(x=>x[0]==='select'&&x[1]==='b'&&x[2]===true));assert.deepEqual(calls[0].slice(0,3),['request','a','delete']);assert.equal(calls[0][3].confirm_title,'正在听的歌');});
test('deleting while paused never starts the next song without intent',async()=>{const {context:c,calls}=page(true);await c.confirmDelete();assert.equal(c.playing,null);assert.ok(calls.some(x=>x[0]==='select'&&x[1]==='b'&&x[2]===false));});
test('failed deletion keeps song and reports error, without pretending cleanup succeeded',async()=>{const {context:c,calls,elements}=page();c.mutateTrack=async()=>{throw Error('磁盘操作失败');};await c.confirmDelete();assert.equal(c.library.tracks.length,2);assert.equal(c.playing,'a');assert.equal(elements.get('delete-error').textContent,'磁盘操作失败');assert.equal(c.deleteBusy,false);assert.equal(elements.get('confirm-delete').disabled,false);assert.ok(!calls.some(x=>x[0]==='stop'||x[0]==='close'));});
test('a deletion in another tab clears stale playback and lyric selection on refresh',()=>{const {context:c,calls}=page();c.library.tracks=c.library.tracks.filter(t=>t.id!=='a');c.reconcileSelection();assert.equal(c.playing,null);assert.equal(c.selected,null);assert.ok(calls.some(x=>x[0]==='stop'));});
