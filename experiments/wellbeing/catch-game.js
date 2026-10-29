// A desktop game with actual pursuit and a two-way possession state machine.
export class CatchGame {
  constructor(width=1000,height=700,random=Math.random) { this.width=width;this.height=height;this.random=random;this.reset() }
  reset() {
    this.phase='ready';this.elapsed=0;this.time=0;this.score=0;this.petCatches=0
    this.pet={x:this.width*.74,y:this.height*.58};this.ball={x:this.width*.25,y:this.height*.72,vx:0,vy:0}
    this.cursor={x:this.ball.x,y:this.ball.y};this.message='Flick the ball. Squipi will chase it!'
  }
  setCursor(x,y) { this.cursor={x,y} }
  drag(x,y) { if(this.phase==='ready')this.ball={x,y,vx:0,vy:0} }
  throwBall(vx,vy) {
    if(this.phase!=='ready')return false
    if(Math.hypot(vx,vy)<80){const dx=this.pet.x-this.ball.x,dy=this.pet.y+42-this.ball.y,d=Math.hypot(dx,dy)||1;vx=dx/d*460;vy=dy/d*460}
    const speed=Math.hypot(vx,vy),scale=Math.min(1,1000/speed)
    this.ball.vx=vx*scale;this.ball.vy=vy*scale;this.phase='chasing';this.time=0;this.message='Go, Squipi!';return true
  }
  catchBall() {
    if(this.phase!=='returning')return false
    this.score++;this.phase='caught';this.time=0;this.message='Caught it! Your turn to throw.';return true
  }
  finish() { this.phase='finished';this.message='Break complete. Thanks for playing!' }
  tick(dt) {
    if(this.phase==='finished')return
    dt=Math.max(0,Math.min(.06,dt));this.elapsed+=dt;this.time+=dt
    if(this.elapsed>=180){this.finish();return}
    const clamp=(v,min,max)=>Math.max(min,Math.min(max,v))
    if(this.phase==='chasing') {
      this.ball.x+=this.ball.vx*dt;this.ball.y+=this.ball.vy*dt
      for(const [axis,speed,limit] of [['x','vx',this.width],['y','vy',this.height]]){
        if(this.ball[axis]<90||this.ball[axis]>limit-90){this.ball[axis]=clamp(this.ball[axis],90,limit-90);this.ball[speed]*=-.7}
        this.ball[speed]*=Math.exp(-.45*dt)
      }
      const dx=this.ball.x-this.pet.x,dy=this.ball.y-(this.pet.y+42),distance=Math.hypot(dx,dy)
      const step=Math.min(distance,440*dt)
      if(distance){this.pet.x+=dx/distance*step;this.pet.y+=dy/distance*step}
      if(distance<36&&this.time>.18){this.petCatches++;this.phase='holding';this.time=0;this.message='Got it! Squipi is throwing it back…'}
    } else if(this.phase==='holding'||this.phase==='windup') {
      this.ball.x=this.pet.x;this.ball.y=this.pet.y+42;this.ball.vx=0;this.ball.vy=0
      if(this.phase==='holding'&&this.time>.8){this.phase='windup';this.time=0}
      else if(this.phase==='windup'&&this.time>.45){
        this.phase='returning';this.time=0;this.from={x:this.ball.x,y:this.ball.y}
        this.to={x:clamp(this.cursor.x,65,this.width-65),y:clamp(this.cursor.y,120,this.height-75)}
        if(Math.hypot(this.to.x-this.from.x,this.to.y-this.from.y)<180)this.to={x:this.pet.x>this.width/2?this.width*.2:this.width*.8,y:this.height*.75}
        this.message='Your catch! Click the ball (or press Space).'
      }
    } else if(this.phase==='returning') {
      const t=Math.min(1,this.time/2)
      this.ball.x=this.from.x+(this.to.x-this.from.x)*t
      this.ball.y=this.from.y+(this.to.y-this.from.y)*t-Math.sin(t*Math.PI)*75
      if(this.time>2.55){this.phase='missed';this.time=0;this.message='Nearly! Pick up the ball and try again.'}
    } else if(['caught','missed'].includes(this.phase)&&this.time>.65){this.phase='ready';this.message='Your throw. Flick the ball anywhere!'}
  }
}

export function mountCatchGame(pet, width, height, theme) {
  const game=new CatchGame(width,height),canvas=document.getElementById('c')
  canvas.style.position='absolute';canvas.style.pointerEvents='none'
  const surface=document.createElement('section');surface.setAttribute('aria-label','Play with Squipi');surface.tabIndex=0
  surface.style.cssText=`position:absolute;inset:0;width:${width}px;height:${height}px;font:14px -apple-system,sans-serif;color:#173e68;touch-action:none;outline:none`
  const status=document.createElement('div');status.setAttribute('aria-live','polite');status.style.cssText='position:absolute;top:22px;left:50%;transform:translateX(-50%);padding:12px 20px;background:#fffffff0;border-radius:20px;box-shadow:0 3px 18px #173e6822;text-align:center;pointer-events:none'
  const ball=document.createElement('button');ball.setAttribute('aria-label','Throw or catch ball');ball.style.cssText='position:absolute;width:34px;height:34px;border:2px solid #173e68;border-radius:50%;background:radial-gradient(circle at 30% 25%,#fff8cf 0%,#edb44e 45%,#db9440 100%);cursor:grab;transform:translate(-50%,-50%);box-shadow:0 3px 6px #173e6833;touch-action:none'
  const controls=document.createElement('div');controls.style.cssText='position:absolute;bottom:18px;left:50%;transform:translateX(-50%);display:flex;gap:8px'
  const pause=document.createElement('button');pause.textContent='Pause'
  const restart=document.createElement('button');restart.textContent='Play again'
  const end=document.createElement('button');end.textContent='End break · Esc'
  const accent={blue:'#286b98',lavender:'#7150a0',lime:'#477000'}[theme]||'#286b98'
  for(const button of [pause,restart,end])button.style.cssText=`border:0;border-radius:18px;padding:9px 16px;background:${accent};color:white;font:inherit;cursor:pointer;box-shadow:0 2px 8px #173e6822`
  controls.append(pause,restart,end);surface.append(status,ball,controls);document.body.append(surface);surface.focus()
  let paused=false,dragging=false,samples=[],last=performance.now(),pose='',lastPet={...game.pet}
  const coords=e=>{const r=surface.getBoundingClientRect();return{x:e.clientX-r.left,y:e.clientY-r.top,t:performance.now()}}
  surface.addEventListener('pointermove',e=>{const p=coords(e);game.setCursor(p.x,p.y);if(dragging){samples.push(p);samples=samples.filter(s=>p.t-s.t<120);game.drag(p.x,p.y)}})
  ball.addEventListener('pointerdown',e=>{
    if(paused)return;ball.focus();e.preventDefault()
    if(game.phase==='returning'){game.catchBall();return}
    if(game.phase!=='ready')return
    dragging=true;samples=[coords(e)];ball.setPointerCapture(e.pointerId)
  })
  ball.addEventListener('pointerup',e=>{
    if(!dragging)return;dragging=false;const p=coords(e),a=samples[0]||p,seconds=Math.max(.016,(p.t-a.t)/1000)
    game.throwBall((p.x-a.x)/seconds,(p.y-a.y)/seconds);samples=[]
  })
  ball.addEventListener('pointercancel',()=>{dragging=false;samples=[]})
  const keyboard=()=>{if(paused)return;if(game.phase==='ready')game.throwBall(0,0);else game.catchBall()}
  ball.addEventListener('click',e=>{if(e.detail===0)keyboard()})
  end.onclick=()=>{game.finish();if(window.webkit?.messageHandlers?.gameControl)window.webkit.messageHandlers.gameControl.postMessage('close')}
  surface.addEventListener('keydown',e=>{if(e.code==='Escape'){e.preventDefault();end.click()}else if(e.code==='Space'&&![pause,restart,end,ball].includes(e.target)){e.preventDefault();keyboard()}})
  pause.onclick=()=>{paused=!paused;pause.textContent=paused?'Resume':'Pause';dragging=false;last=performance.now()}
  restart.onclick=()=>{game.reset();paused=false;pause.textContent='Pause';last=performance.now();surface.focus()}
  function frame(now){
    const dt=(now-last)/1000;last=now
    if(!paused&&!document.hidden&&!dragging)game.tick(dt)
    ball.style.left=`${game.ball.x}px`;ball.style.top=`${game.ball.y}px`;ball.disabled=paused;ball.hidden=game.phase==='finished'
    canvas.style.left=`${game.pet.x-160}px`;canvas.style.top=`${game.pet.y-160}px`
    const next=game.phase==='chasing'?'chasing':['holding','windup'].includes(game.phase)?game.phase:game.phase==='returning'&&game.time<.35?'throwing':'idle'
    if(next!==pose){pet.setPlayPose(next);pose=next}
    pet.lookAt(Math.max(-1,Math.min(1,(game.ball.x-game.pet.x)/160)),Math.max(-1,Math.min(1,-(game.ball.y-game.pet.y)/160)))
    pet.setVelocity((game.pet.x-lastPet.x)*.01,(game.pet.y-lastPet.y)*.01);lastPet={...game.pet}
    const text=paused?'Paused. Take your time.':`${game.message}  You ${game.score} · Squipi ${game.petCatches}`
    if(status.textContent!==text)status.textContent=text
    requestAnimationFrame(frame)
  }
  requestAnimationFrame(frame)
}
