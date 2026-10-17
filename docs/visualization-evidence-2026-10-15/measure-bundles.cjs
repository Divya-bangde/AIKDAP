// Usage: node measure-bundles.cjs DIST OUT.json. Sizes include actual gzip bytes.
const fs=require('node:fs');const path=require('node:path');const zlib=require('node:zlib');
const root=path.resolve(process.argv[2]);
const files=fs.readdirSync(path.join(root,'assets')).filter(name=>/\.(js|css)$/.test(name)).map(name=>{
  const content=fs.readFileSync(path.join(root,'assets',name));
  return {file:name,rawBytes:content.length,gzipBytes:zlib.gzipSync(content).length};
});
const groups={plotly2D:files.filter(f=>/^plotly-2d.*\.(js|css)$/.test(f.file)),plotly3D:files.filter(f=>/^plotly-3d.*\.js$/.test(f.file)),plotlyWrapper:files.filter(f=>/^PlotlyVisualization.*\.js$/.test(f.file)),equation:files.filter(f=>/^InteractiveEquationCard.*\.js$/.test(f.file)),mermaid:files.filter(f=>/^MermaidDiagram.*\.js$/.test(f.file))};
fs.writeFileSync(process.argv[3],JSON.stringify({units:'bytes; decimal kB=bytes/1000; gzip via Node default settings',dist:root,groups,allAssets:files},null,2));
console.log(JSON.stringify(groups,null,2));
