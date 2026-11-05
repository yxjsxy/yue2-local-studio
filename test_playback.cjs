const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(process.argv[2]||'studio-ui/library.js','utf8');
const {createSongPlayer}=vm.runInNewContext(source.slice(0,source.indexOf('function songDownloadName'))+';({createSongPlayer})',{Promise,setTimeout,clearTimeout});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){
 const listeners={},calls=[],states=[],timers=new Map();let sequence=0,ends=0;
 const media={dataset:{},paused:true,ended:false,currentTime:12,duration:200,error:null,src:'',
 getAttribute(){return this.src;},removeAttribute(){this.src='';},pause(){this.paused=true;this.emit('pause');},addEventListener(e,f){listeners[e]=f;},
 play(){this.paused=false;return new Promise((resolve,reject)=>calls.push({resolve,reject}));},
 load(){this.error=null;this.currentTime=0;this.paused=true;},
 emit(e){listeners[e]?.();}};
 const player=createSongPlayer(media,{status:(message,state)=>states.push({message,state}),ended:()=>ends++},{set:f=>{timers.set(++sequence,f);return sequence;},clear:id=>timers.delete(id)});
 return {media,player,calls,states,timers,ends:()=>ends,run(){const callbacks=[...timers.values()];timers.clear();callbacks.forEach(f=>f());}};
}
test('late rejection from previous song cannot stop a successful successor',async()=>{const f=fixture();f.player.start('/a.mp3');f.player.start('/b.mp3');f.media.emit('playing');f.calls[0].reject({name:'AbortError'});await tick();assert.equal(f.states.at(-1).state,'playing');assert.equal(f.media.src,'/b.mp3');assert.equal(f.timers.size,0);});
test('permission denial is truthful, no automatic retry; explicit resume clears it',async()=>{const f=fixture();f.player.start('/a.mp3');f.calls[0].reject({name:'NotAllowedError'});await tick();assert.equal(f.states.at(-1).state,'blocked');assert.equal(f.timers.size,0);f.player.start('/a.mp3');f.media.emit('playing');assert.equal(f.states.at(-1).message,'');});
test('network failure retries once and restores position, duplicate errors share retry',async()=>{const f=fixture();f.player.start('/a.mp3');f.media.error={code:2};f.media.emit('error');f.calls[0].reject({name:'NotSupportedError'});await tick();assert.equal(f.timers.size,1);f.run();assert.equal(f.calls.length,2);f.media.emit('loadedmetadata');assert.equal(f.media.currentTime,12);f.calls[1].reject({name:'NotSupportedError'});await tick();assert.equal(f.states.at(-1).state,'failed');assert.equal(f.timers.size,0);});
test('manual pause cancels pending play and cannot restart from late rejection',async()=>{const f=fixture();f.player.start('/a.mp3');f.media.paused=true;f.media.emit('pause');f.calls[0].reject({name:'AbortError'});await tick();assert.equal(f.states.at(-1).state,'paused');assert.equal(f.timers.size,0);assert.equal(f.media.autoplay,false);});
test('natural ending retains intent and advances playlist, pause does not',()=>{const f=fixture();f.player.start('/a.mp3');f.media.emit('playing');f.media.paused=true;f.media.ended=true;f.media.emit('pause');f.media.emit('ended');assert.equal(f.ends(),1);f.media.ended=false;f.media.emit('pause');f.media.emit('ended');assert.equal(f.ends(),1);});
test('switching song cancels scheduled recovery for the old source',()=>{const f=fixture();f.player.start('/a.mp3');f.media.error={code:2};f.media.emit('error');f.player.start('/b.mp3');f.run();assert.equal(f.calls.length,2);assert.equal(f.media.src,'/b.mp3');});

test('deleting a playing song clears its source and prevents late recovery',async()=>{const f=fixture();f.player.start('/a.mp3');f.player.stop();f.calls[0].reject({name:'AbortError'});await tick();assert.equal(f.media.src,'');assert.equal(f.media.autoplay,false);assert.equal(f.timers.size,0);assert.equal(f.states.at(-1).state,'idle');f.media.emit('ended');assert.equal(f.ends(),0);});
