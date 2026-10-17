exports.contrast = async function(page) {
  return page.evaluate(() => {
    const root=document.documentElement;
    const color=name=>{const e=document.createElement('span');const raw=getComputedStyle(root).getPropertyValue(name).trim();e.style.color=raw.startsWith('#')?raw:`hsl(${raw})`;document.body.append(e);const c=getComputedStyle(e).color.match(/[\d.]+/g).slice(0,3).map(Number);e.remove();return c;};
    const lum=c=>c.map(v=>{v/=255;return v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4;}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
    const ratio=(a,b)=>{const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
    const card=color('--card'),sunken=color('--sunken');
    const surfaces={card,sunken,background:color('--background'),researchComposite:sunken.map((v,i)=>v*.4+card[i]*.6)};
    const marks=Object.fromEntries(Array.from({length:8},(_,i)=>[`--chart-${i+1}`,color(`--chart-${i+1}`)]));
    Object.assign(marks,{'graph-uploaded':color('--primary'),'graph-suggested':color('--success'),'graph-external-and-edge':color('--muted-foreground')});
    return {theme:root.classList.contains('dark')?'dark':'light',surfaces,marks,ratios:Object.fromEntries(Object.entries(marks).map(([name,c])=>[name,Object.fromEntries(Object.entries(surfaces).map(([key,bg])=>[key,Number(ratio(c,bg).toFixed(3))]))]))};
  });
};
exports.simulation = async function(page,path,kind) {
  await page.evaluate(kind=>{
    const old=document.getElementById('vision-simulation');if(old)old.remove();
    const main=document.querySelector('main');
    if(kind==='grayscale'){main.style.filter='grayscale(1)';return;}
    const values=kind==='deuteranopia'?'0.367322 0.860646 -0.227968 0 0 0.280085 0.672501 0.047413 0 0 -0.011820 0.042940 0.968881 0 0 0 0 0 1 0':'0.152286 1.052583 -0.204868 0 0 0.114503 0.786281 0.099216 0 0 -0.003882 -0.048116 1.051998 0 0 0 0 0 1 0';
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.id='vision-simulation';svg.style.cssText='position:absolute;width:0;height:0';
    svg.innerHTML=`<filter id="vision-filter" color-interpolation-filters="linearRGB"><feColorMatrix type="matrix" values="${values}" /></filter>`;
    document.body.append(svg);main.style.filter='url(#vision-filter)';
  },kind);
  await page.screenshot({path,fullPage:true});
  await page.evaluate(()=>{document.querySelector('main').style.filter='';document.getElementById('vision-simulation')?.remove();});
};
