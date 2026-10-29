import {test} from 'node:test'
import assert from 'node:assert/strict'
import {CatchGame} from './catch-game.js'
const advance=(g,n)=>{for(let i=0;i<n*40;i++)g.tick(.025)}
test('Squipi pursues and catches the thrown ball, then throws to the user',()=>{
 const g=new CatchGame(1000,700,()=>.5),start={...g.pet}
 g.throwBall(600,-230);advance(g,.4)
 assert.notDeepEqual(g.pet,start);assert.equal(g.phase,'chasing')
 for(let n=0;n<400&&g.phase!=='returning';n++)g.tick(.025)
 assert.equal(g.phase,'returning');assert.equal(g.petCatches,1)
 assert.equal(g.catchBall(),true);assert.equal(g.catchBall(),false);assert.equal(g.score,1)
 advance(g,1);assert.equal(g.phase,'ready')
 assert.equal(g.throwBall(-500,0),true)
 for(let n=0;n<400&&g.phase!=='returning';n++)g.tick(.025)
 advance(g,4);assert.equal(g.phase,'ready');assert.equal(g.score,1)
 advance(g,180);assert.equal(g.phase,'finished');assert.equal(g.throwBall(100,100),false)
 g.reset();assert.equal(g.score,0)
})
