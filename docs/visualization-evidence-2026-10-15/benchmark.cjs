// Invoked with an existing Playwright browser. All data is synthetic/local.
exports.measure = async function(browser, baseURL, run, entry='visualization-audit.html') {
  const context=await browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:1});
  const page=await context.newPage(); const errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  const cdp=await context.newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
  await cdp.send('Network.enable'); await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
  await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:40,downloadThroughput:1250000,uploadThroughput:625000});
  await cdp.send('Performance.enable');
  let start=Date.now();
  await page.goto(baseURL+'/'+entry,{waitUntil:'domcontentloaded'});
  await page.getByRole('button',{name:'Research plot',exact:true}).click();
  await page.locator('.js-plotly-plot .main-svg').first().waitFor({timeout:30000});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const initialPlotlyMs=Date.now()-start;
  const m1=Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(v=>[v.name,v.value]));
  start=Date.now(); await page.getByRole('button',{name:'Equation calculator',exact:true}).click();
  await page.locator('input[type=range]').first().waitFor({timeout:30000});
  await page.locator('.js-plotly-plot .main-svg').first().waitFor({timeout:30000});
  const equationAdditionalLoadMs=Date.now()-start;
  start=Date.now(); await page.locator('input[type=range]').first().focus(); await page.keyboard.press('ArrowRight');
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const equationKeyboardMs=Date.now()-start;
  start=Date.now(); await page.getByRole('button',{name:'Diagram',exact:true}).click();
  await page.locator('svg[id^=mermaid]').waitFor({timeout:30000});
  const mermaidColdMs=Date.now()-start;
  const m2=Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(v=>[v.name,v.value]));
  const result={run,initialPlotlyMs,equationAdditionalLoadMs,equationKeyboardMs,mermaidColdMs,initialScriptMs:m1.ScriptDuration*1000,totalScriptMs:m2.ScriptDuration*1000,totalTaskMs:m2.TaskDuration*1000,errors};
  await context.close();return result;
};

exports.compileTrace = async function(browser, baseURL, entry='visualization-audit.html') {
  const context=await browser.newContext({viewport:{width:1440,height:900}}); const page=await context.newPage();
  const cdp=await context.newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
  await cdp.send('Network.enable');await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
  await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:40,downloadThroughput:1250000,uploadThroughput:625000});
  const events=[];cdp.on('Tracing.dataCollected',event=>events.push(...event.value));
  await cdp.send('Tracing.start',{categories:'v8,disabled-by-default-v8.compile,devtools.timeline',transferMode:'ReportEvents'});
  await page.goto(baseURL+'/'+entry,{waitUntil:'domcontentloaded'});
  await page.getByRole('button',{name:'Research plot',exact:true}).click();
  await page.locator('.js-plotly-plot .main-svg').first().waitFor({timeout:30000});
  const complete=new Promise(resolve=>cdp.once('Tracing.tracingComplete',resolve));
  await cdp.send('Tracing.end');await complete;
  const names={};
  for(const event of events) if(event.ph==='X' && /parse|compile/i.test(event.name)) {
    const entry=names[event.name]??={count:0,durationMs:0};entry.count++;entry.durationMs+=(event.dur||0)/1000;
  }
  await context.close();return {note:'Inclusive trace-event durations; nested categories overlap and must not be summed. Same cold 4xCPU/10Mbps/40ms profile. Separate run from untraced runtime samples.',events:names};
};
