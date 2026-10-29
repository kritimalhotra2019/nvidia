import path from 'node:path'
import {mkdir} from 'node:fs/promises'
import { launchBrowser } from './browser.js'
const browser = await launchBrowser({args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']})
const page = await browser.newPage({viewport:{width:520,height:520},deviceScaleFactor:1})
const errors=[]
page.on('pageerror', e=>errors.push(e.message))
await mkdir('shots/wardrobe',{recursive:true})
for (const [theme,outfit,hat,state] of [['blue','coat','none','working'],['lavender','scarf','beret','reading'],['lime','bowtie','beanie','idle']]) {
 await page.goto('file://'+path.resolve('dist/index.html')+`?size=520&white=1&theme=${theme}&outfit=${outfit}&hat=${hat}&state=${state}`)
 await page.waitForTimeout(1000)
 await page.screenshot({path:`shots/wardrobe/${theme}.png`})
 // Exercise repeated switching, including all optional accessories off.
 await page.evaluate(()=>{pet.setWardrobe({theme:'lavender',outfit:'classic',hat:'none',glasses:false});pet.setWardrobe({theme:'blue',outfit:'coat',hat:'beret',glasses:true})})
}
await browser.close()
if(errors.length) throw Error(errors.join('\n'))
console.log('Three themes and wardrobe switching rendered without browser errors.')
