import * as THREE from 'three'

// Clothing follows the unchanged mantle/web surface. All hems stay above the arm tips.
export function makeClothes(toon) {
  const clothes = {}, accessories = {}
  const material = (colour, map) => new THREE.MeshToonMaterial({color:colour, map:map || null, gradientMap:toon, side:THREE.DoubleSide})
  const ivory = material(0xf6f0e3), ink = material(0x293b50), gold = material(0xcba76a), blue = material(0x416589)
  function radius(y, theta) {
    const mantle = 1.08 * Math.sqrt(Math.max(0,1-((y-.12)/1.0)**2))
    const lobe = (.5+.5*Math.cos((Math.PI/2-theta)*8))**1.6
    const webPoint = u => {
      const y0=-.22-.78*u+.12*Math.sin(u*Math.PI)
      const w=Math.min(1,Math.max(0,(-.22-y0)/.9))**2
      return {y:y0-.20*lobe*w+.05*(1-lobe)*w,r:(.58+.72*Math.sin(u*Math.PI/2))*(1+.22*lobe*w-.09*(1-lobe)*w)}
    }
    let low=0,high=1
    for(let i=0;i<18;i++){const mid=(low+high)/2;if(webPoint(mid).y>y)low=mid;else high=mid}
    const web=webPoint((low+high)/2).r
    return Math.max(mantle,web)+.075
  }
  function point(theta,y,push=0) { const r=radius(y,theta)+push; return new THREE.Vector3(Math.sin(theta)*r,y,Math.cos(theta)*r) }
  function surface(group,mat,top,bottom,start=-Math.PI,end=Math.PI,split=false,trim=ink,offset=0) {
    const positions=[],uv=[],indices=[],columns=96,rows=18
    for(let row=0;row<=rows;row++) for(let col=0;col<=columns;col++) {
      const v=row/rows,theta=start+(end-start)*col/columns
      const notch=split ? .12*Math.exp(-Math.pow(theta/.10,2))*v**5 : 0
      const y=top+(bottom-top)*v+notch
      positions.push(...point(theta,y,offset).toArray());uv.push((theta+Math.PI)/(2*Math.PI)*4,1-v)
      if(row<rows&&col<columns) {const a=row*(columns+1)+col,b=a+columns+1;indices.push(a,b,a+1,b,b+1,a+1)}
    }
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));geometry.setIndex(indices);geometry.computeVertexNormals()
    group.add(new THREE.Mesh(geometry,mat))
    line(group,Array.from({length:65},(_,i)=>[start+(end-start)*i/64,top]),trim,.009)
    line(group,Array.from({length:65},(_,i)=>[start+(end-start)*i/64,bottom+(split?.12*Math.exp(-Math.pow((start+(end-start)*i/64)/.10,2)):0)]),trim,.009)
  }
  function patch(group,points,mat,push=.012) {
    const geometry=new THREE.BufferGeometry(), positions=points.flatMap(([a,y])=>point(a,y,push).toArray()),indices=[]
    for(let i=1;i<points.length-1;i++)indices.push(0,i,i+1)
    geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setIndex(indices);geometry.computeVertexNormals();group.add(new THREE.Mesh(geometry,mat))
  }
  function line(group,points,mat=gold,width=.009) {
    const curve=new THREE.CatmullRomCurve3(points.map(([a,y])=>point(a,y,.018)))
    group.add(new THREE.Mesh(new THREE.TubeGeometry(curve,Math.max(12,points.length*2),width,6,false),mat))
  }
  function button(group,a,y) {const mesh=new THREE.Mesh(new THREE.SphereGeometry(.023,12,10),gold);mesh.position.copy(point(a,y,.03));group.add(mesh)}
  function pocket(group,a,y) {
    patch(group,[[a-.085,y],[a+.085,y],[a+.075,y-.13],[a-.075,y-.13]],ivory,.026)
    line(group,[[a-.085,y],[a+.085,y]],blue,.009)
  }
  // Original geometric artwork informed by Indian Ajrak block-printed textiles:
  // interlocking madder rosettes, ink crosses and fine resist-coloured outlines.
  function ajrak() {
    const canvas=document.createElement('canvas');canvas.width=canvas.height=512
    const c=canvas.getContext('2d'), cream='#e9dcc0', indigo='#233e58', red='#943b32', black='#17252d'
    c.fillStyle=indigo;c.fillRect(0,0,512,512)
    function polygon(x,y,r,n,fill,stroke=cream,inner=r,rotation=-Math.PI/2) {
      c.beginPath()
      for(let i=0;i<n*2;i++){const a=rotation+i*Math.PI/n,rr=i%2?inner:r;c.lineTo(x+Math.cos(a)*rr,y+Math.sin(a)*rr)}
      c.closePath();c.fillStyle=fill;c.fill();c.strokeStyle=stroke;c.lineWidth=2.5;c.stroke()
    }
    function dot(x,y,r=2){c.beginPath();c.arc(x,y,r,0,Math.PI*2);c.fillStyle=cream;c.fill()}
    for(let y=-128;y<=640;y+=256)for(let x=-128;x<=640;x+=256){
      polygon(x,y,94,8,red,cream,78,Math.PI/8)
      polygon(x,y,22,8,indigo,cream,12)
      for(let i=0;i<8;i++){
        const a=i*Math.PI/4,px=x+Math.cos(a)*54,py=y+Math.sin(a)*54
        polygon(px,py,16,6,indigo,cream,11);dot(px,py,3)
        const b=a+Math.PI/8;polygon(x+Math.cos(b)*79,y+Math.sin(b)*79,7,4,black,cream,4)
      }
      // Four-lobed black motifs fill the spaces between the red medallions.
      const bx=x+128,by=y+128
      polygon(bx,by,84,4,black,cream,29,0)
      polygon(bx,by,28,8,indigo,cream,17);dot(bx,by,5)
      for(let i=0;i<4;i++){
        const a=i*Math.PI/2
        for(const r of [46,58,70])dot(bx+Math.cos(a)*r,by+Math.sin(a)*r,1.8)
      }
      for(const [dx,dy] of [[128,0],[0,128]]){
        polygon(x+dx,y+dy,26,4,indigo,cream,14)
        polygon(x+dx,y+dy,9,4,red,cream,5)
      }
    }
    const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace
    texture.wrapS=texture.wrapT=THREE.RepeatWrapping
    // UVs use four repeats around the body; compensate to keep motifs square.
    texture.repeat.set(2.4,1);texture.anisotropy=8;return texture
  }
  const printed=material(0xffffff,ajrak())
  for(const [name,mat,length] of [['shirt',ivory,-.61],['dress',material(0x4d687f),-.97],['kurta',material(0xc7ab78),-.88],['ajrakKurta',printed,-.99],['ajrakDress',printed,-.97]]) {
    const group=new THREE.Group();surface(group,mat,-.20,length)
    if(name.toLowerCase().includes('dress')) {
      surface(group,gold,-.43,-.475)
      patch(group,[[-.09,-.435],[-.22,-.40],[-.22,-.51]],ivory,.03)
      patch(group,[[.09,-.435],[.22,-.40],[.22,-.51]],ivory,.03)
    } else {
      // A small collar and placket sit below the smile, never over the face.
      patch(group,[[-.02,-.21],[-.22,-.20],[-.11,-.36]],ivory,.02)
      patch(group,[[.02,-.21],[.22,-.20],[.11,-.36]],ivory,.02)
      line(group,[[0,-.24],[0,-.55]],gold,.012)
      for(const y of [-.31,-.42,-.53])button(group,0,y)
    }
    if(name.toLowerCase().includes('kurta'))line(group,[[0,-.65],[0,length]],gold,.009)
    clothes[name]=group
  }
  for(const [name,bottom] of [['shorts',-.83],['pants',-1.03]]) {
    const group=new THREE.Group();surface(group,blue,-.50,bottom,-Math.PI,Math.PI,true);surface(group,ink,-.50,-.56)
    for(const side of [-1,1]) {line(group,[[side*.21,-.57],[side*.17,bottom+.025]],gold,.007);line(group,[[side*.42,-.56],[side*.52,-.64],[side*.69,-.65]],gold,.007)}
    button(group,0,-.535);clothes[name]=group
  }
  // Jensen Huang's standing-collar leather jacket, adapted to the unchanged body.
  // Procedural embossing is deliberately subtle; the silhouette and silver zips read first.
  const grainCanvas=document.createElement('canvas');grainCanvas.width=grainCanvas.height=256
  const grain=grainCanvas.getContext('2d');grain.fillStyle='#777777';grain.fillRect(0,0,256,256)
  for(let row=0;row<32;row++)for(let col=-1;col<16;col++){
    const x=col*18+(row%2)*9,y=row*8
    grain.fillStyle=`rgb(${122+(row*7+col*11)%14},${122+(row*7+col*11)%14},${122+(row*7+col*11)%14})`
    grain.fillRect(x+1,y+1,16,6);grain.strokeStyle='#686868';grain.lineWidth=.5;grain.strokeRect(x+1,y+1,16,6)
  }
  const emboss=new THREE.CanvasTexture(grainCanvas);emboss.wrapS=emboss.wrapT=THREE.RepeatWrapping;emboss.repeat.set(3,1);emboss.anisotropy=4
  const leather=new THREE.MeshPhysicalMaterial({color:0x202125,roughness:.43,metalness:.08,clearcoat:.35,clearcoatRoughness:.38,bumpMap:emboss,bumpScale:.008,side:THREE.DoubleSide})
  const leatherEdge=material(0x393a3e), blackTee=material(0x111216)
  const silver=new THREE.MeshStandardMaterial({color:0xb8bec4,metalness:.7,roughness:.3})
  const jacket=new THREE.Group()
  surface(jacket,blackTee,-.21,-.91,-Math.PI,Math.PI,false,blackTee,-.025)
  for(const side of [-1,1]){
    surface(jacket,leather,-.21,-.86,side>0?.12:-Math.PI,side>0?Math.PI:-.12,false,leatherEdge)
    // Short band collar, open at the front, with a silver snap.
    surface(jacket,leather,-.17,-.29,side>0?.14:-Math.PI,side>0?Math.PI:-.14,false,leatherEdge,.012)
    line(jacket,[[side*.125,-.28],[side*.12,-.85]],silver,.005)
    for(let y=-.32;y>-.84;y-=.028)line(jacket,[[side*.10,y],[side*.135,y]],silver,.004)
    const snap=new THREE.Mesh(new THREE.SphereGeometry(.018,12,8),silver);snap.position.copy(point(side*.20,-.235,.033));jacket.add(snap)
    // Slanted chest pockets and smaller hand pockets match the reference jacket.
    line(jacket,[[side*.30,-.43],[side*.67,-.39]],blackTee,.025)
    line(jacket,[[side*.30,-.43],[side*.67,-.39]],silver,.006)
    line(jacket,[[side*.63,-.40],[side*.65,-.455]],silver,.006)
    line(jacket,[[side*.70,-.59],[side*.65,-.74]],blackTee,.018)
    line(jacket,[[side*.70,-.59],[side*.65,-.74]],silver,.004)
    line(jacket,[[side*.91,-.25],[side*.89,-.52],[side*.96,-.80]],leatherEdge,.007)
  }
  surface(jacket,leather,-.80,-.88,-Math.PI,Math.PI,false,leatherEdge,.008)
  clothes.jensenJacket=jacket
  const coat=new THREE.Group()
  surface(coat,ivory,-.20,-.91,.12,Math.PI);surface(coat,ivory,-.20,-.91,-Math.PI,-.12)
  for(const side of [-1,1]) {
    patch(coat,[[side*.13,-.20],[side*.39,-.20],[side*.16,-.47]],material(0xd6e4e6),.02)
    pocket(coat,side*.48,-.57);button(coat,side*.16,-.54);button(coat,side*.16,-.74)
  }
  // A tiny pen on the pocket makes the coat readable without a blocky badge.
  line(coat,[[.50,-.54],[.50,-.64]],blue,.012);clothes.coat=coat
  const scarf=new THREE.Group();surface(scarf,gold,-.20,-.31)
  patch(scarf,[[.24,-.29],[.43,-.29],[.48,-.70],[.29,-.72]],gold,.035)
  for(const y of [-.58,-.63,-.68])line(scarf,[[.29,y],[.46,y]],ivory,.009)
  const knot=new THREE.Mesh(new THREE.SphereGeometry(.10,24,16),gold);knot.scale.set(1,.7,.6);knot.position.copy(point(.32,-.30,.065));scarf.add(knot);accessories.scarf=scarf
  const bow=new THREE.Group();patch(bow,[[-.025,-.24],[-.20,-.20],[-.20,-.37]],gold,.04);patch(bow,[[.025,-.24],[.20,-.20],[.20,-.37]],gold,.04)
  const centre=new THREE.Mesh(new THREE.SphereGeometry(.06,16,12),gold);centre.position.copy(point(0,-.28,.055));bow.add(centre);accessories.bowtie=bow
  return {clothes,accessories}
}
