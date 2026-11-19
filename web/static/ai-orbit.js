'use strict';
const root = '/static/assets/motion/';
const quads = {
 hero: {frame:[946,878], cards:[
  ['窗影暖光','window','window',[[70,462],[239,487],[229,711],[39,723]], [132,599,184,303,6]],
  ['水面折光','water','water',[[207,440],[389,468],[378,712],[185,727]], [282,589,199,329,5]],
  ['香氛主图','bottle','bottle',[[359,396],[587,396],[603,732],[344,732]], [474,566,241,395,0]],
  ['金属瓶盖','metal','metal',[[577,464],[738,440],[755,728],[591,716]], [674,585,198,326,-5]],
  ['香氛与包装','box','box',[[711,489],[872,462],[905,725],[726,711]], [813,596,184,303,-6]],
 ]},
 examples: {frame:[946,331],cards:[
  ['暖岩场景','sand','window',[[33,110],[326,110],[326,307],[33,307]],[179,209,290,196,0]],
  ['水波场景','pool','water',[[326,110],[618,110],[618,307],[326,307]],[472,209,290,196,0]],
  ['缎面场景','silk','silk',[[618,110],[911,110],[911,307],[618,307]],[765,209,290,196,0]]
 ]},
 features: {frame:[946,416],cards:[
  ['金属与玻璃特写','macro','metal',[[184,136],[422,146],[469,349],[469,416],[26,416],[55,360]],[239,226,440,388,0]],
  ['套图：暖岩','sand','window',[[520,180],[671,150],[710,238],[552,270]],[618,210,180,119,-9]],
  ['套图：水波','pool','water',[[685,147],[839,120],[885,206],[725,237]],[785,178,180,120,-9]],
  ['套图：缎面','silk','silk',[[551,282],[714,250],[756,347],[590,383]],[650,316,190,125,-9]],
  ['套图：包装','package-landscape','box',[[731,246],[881,218],[932,313],[773,346]],[830,280,190,125,-9]],
 ]}
};
let current=null;
const reduce=matchMedia('(prefers-reduced-motion: reduce)');
const recVideo=rec=>rec.video;
const clips = new Set(["window","water","bottle","metal","box","sand","pool","silk","macro","package-landscape"]);
const localClips = new Set((document.body.dataset.localClips || "").split(/\s+/).filter(Boolean));
// Use original exports without re-encoding; cap the playback range when appropriate.
const ranges = {window:[0,3.2],bottle:[0,3.35],macro:[3.7,7.2],"package-landscape":[.12,2.77]};
const mouse = matchMedia('(hover:hover) and (pointer:fine)');
const paint=(el,key,value)=>el.style.setProperty(key,value);
function clear(){
 if (!current) return;
 if(recVideo(current)){let v=recVideo(current);v.pause();try{v.currentTime=Number(v.dataset.clipStart||0)}catch{}}
 current.hit.classList.remove('engaged');
 current.card.classList.remove('engaged'); current=null;
}
function activate(rec){
 if(current===rec)return;
 clear(); current=rec;
 rec.hit.classList.add('engaged'); rec.card.classList.add('engaged');
 if(rec.video && !reduce.matches){rec.video.play().then(()=>{if(current!==rec || reduce.matches || document.hidden){rec.video.pause();rec.card.classList.remove('playing');}}).catch(()=>{});}
}
for(const [key, group] of Object.entries(quads)){
 const host=document.querySelector('[data-motion="'+key+'"]');
 const [w,h]=group.frame;
 const targets=document.createElement('div');targets.className='motion-targets';
 const cards=document.createElement('div');cards.className='motion-cards'; cards.setAttribute('aria-hidden','true');
 host.append(cards,targets);
 for(const [name, file, effect, poly, geom] of group.cards){
  const [cx,cy,cw,ch,rot]=geom;
  const xs=poly.map(a=>a[0]),ys=poly.map(a=>a[1]), x=Math.min(...xs),y=Math.min(...ys);
  const dx=Math.max(...xs)-x, dy=Math.max(...ys)-y;
  const hit=document.createElement('button');hit.type='button';hit.className='motion-hit';hit.dataset.panel=name;hit.setAttribute('aria-label',name+'，预览画面');
  for(const [k,n] of Object.entries({left:x/w,top:y/h,width:dx/w,height:dy/h}))hit.style[k]=100*n+'%';
  hit.style.clipPath='polygon('+poly.map(([u,v])=>100*(u-x)/dx+'% '+100*(v-y)/dy+'%').join(',')+')';
  const card=document.createElement('div');card.className='motion-card';card.dataset.effect=effect;
  for(const [k,n] of Object.entries({'--cx':cx/w,'--cy':cy/h,'--w':cw/w,'--h':ch/h}))paint(card,k,(100*n)+'%');
  paint(card,'--rot',rot+'deg');
  const img=document.createElement('img');img.src=root+file+'.webp';img.alt=''; img.decoding='async';
  const sheen=document.createElement('i');sheen.className='motion-sheen';card.append(img,sheen);
  cards.append(card);targets.append(hit);
  const item={hit,card};
  if(clips.has(file) || localClips.has(file)){
   const video=document.createElement('video');
   video.preload='none';video.muted=true;video.defaultMuted=true;video.loop=true;video.playsInline=true;
   video.poster=img.src;video.src = localClips.has(file) ? '/showcase-media/'+file+'-tight.mp4' : root+'orbit/'+file+'.mp4';
   const range = ranges[file];
   if(range){
    const [start,stop]=range;
    video.dataset.clipStart=String(start);
    video.addEventListener('loadedmetadata',()=>{if(start)video.currentTime=start;});
    video.addEventListener('timeupdate',()=>{if(!video.paused && (video.currentTime>=stop || video.currentTime+.1<start))video.currentTime=start;});
   }
   video.setAttribute('aria-hidden','true');
   item.video=video;card.classList.add('ai-water-card');card.append(video);
   video.addEventListener('playing',()=>{if(current===item && !reduce.matches && !document.hidden)card.classList.add('playing');else video.pause()});
   video.addEventListener('pause',()=>card.classList.remove('playing'));
  }
  hit.addEventListener('pointerenter',e=>{if(mouse.matches&&e.pointerType==='mouse')activate(item)});
  hit.addEventListener('pointerleave',()=>{if(current===item)clear()});
  hit.addEventListener('blur',()=>{if(current===item)clear()});
  // Explicit keyboard activation allows access without auto-starting from focus.
  hit.addEventListener('click',e=>{if(e.detail===0){ if(current===item)clear();else activate(item); }});
 }
}
document.addEventListener('keydown',e=>{if(e.key==='Escape')clear()});
document.addEventListener('visibilitychange',()=>{if(document.hidden)clear()});
window.addEventListener('blur',clear);

reduce.addEventListener('change',()=>clear());
