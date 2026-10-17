import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MotionConfig } from 'motion/react';
import '@/index.css';
import { AnalysisCard } from '@/features/business-analytics/AnalysisCard';
import { AnswerVisualization } from '@/features/research/AnswerVisualization';
import { AnswerEquations } from '@/features/research/AnswerEquations';
import PaperMap from '@/features/paper-map/PaperMap';
import { SourceMixBar } from '@/features/research/SourceMixBar';

const client = new QueryClient({defaultOptions:{queries:{staleTime:Infinity,retry:false}}});
const nodes = ['Paper A: comparative methods','Paper B: validation results','Paper C: related study'].map((title,i)=>({id:`W${i+1}`,title,year:2024,cited_by_count:20,kind:'uploaded',in_project:true,doi:null,asset_id:null}));
for (const id of ['audit-two','audit-one']) client.setQueryData(['paper-graph',id,false],{nodes,links:id==='audit-one'?[{source:'W1',target:'W2'}]:[{source:'W1',target:'W2'},{source:'W2',target:'W3'}],unmatched:[],external_unavailable:false});
const rows = [{month:'March',region:'West',revenue:300,cost:150},{month:'January',region:'West',revenue:100,cost:50},{month:'February',region:'West',revenue:200,cost:100},{month:'January',region:'East',revenue:140,cost:90},{month:'February',region:'East',revenue:240,cost:110}];
const base = {id:'audit-analysis',dataset_id:'audit-dataset',question:'Compare revenue and cost by month and region',status:'completed',error:null,narrative:'Synthetic audit fixture. Both revenue and cost are present in the result table.',unverified_numbers:[],plan:{filters:[],time_bucket:null,group_by:['month','region'],metrics:[{column:'revenue',agg:'sum',alias:'revenue',as_share:false},{column:'cost',agg:'sum',alias:'cost',as_share:false}],sort:null,limit:50,chart:{type:'line',x:'month',y:['revenue','cost'],series:'region'}},result:{columns:['month','region','revenue','cost'],rows,total_rows:5}};
const pie = {...base,question:'Show category shares',narrative:'Synthetic fixture: only 2 of 10 category results are returned.',plan:{...base.plan,chart:{type:'pie',x:'category',y:['revenue'],series:null}},result:{columns:['category','revenue'],rows:[{category:'A',revenue:60},{category:'B',revenue:40}],total_rows:10}};
const equation = {label:'Synthetic illustrative fixture: E = m c²',latex:'E = m c^2',expression:'m*c^2',output:'E',variables:{E:{name:'Energy',unit:'J',role:'input',value:90000000000000000,min:1,max:1e18,illustrative:true,latex:'E'},m:{name:'Mass',unit:'kg',role:'input',value:1,min:0.1,max:100,illustrative:true,latex:'m'},c:{name:'Speed of light',unit:'m/s',role:'constant',value:300000000,min:300000000,max:300000000,illustrative:false,latex:'c'}},solutions:{E:{expression:'m*c^2',latex:'m c^2'},m:{expression:'E/c^2',latex:'E/c^2'}}};
const cases=['Analytics comparison','Truncated pie','Research plot','Equation calculator','Paper map','One citation','Diagram'];
function Audit(){
 const [active,setActive]=useState(cases[0]); const [dark,setDark]=useState(false);
 return <main className="mx-auto max-w-6xl p-4 sm:p-8"><h1 className="text-2xl font-semibold">AIKDAP visualization audit</h1><p className="my-2 text-sm text-muted-foreground">Synthetic fixtures rendered through the current production components. No live project data or model calls.</p><div className="my-4 flex flex-wrap gap-2">{cases.map(name=><button className="rounded border border-border px-3 py-2 text-sm" aria-pressed={active===name} onClick={()=>setActive(name)} key={name}>{name}</button>)}<button className="rounded border border-border px-3 py-2 text-sm" onClick={()=>{document.documentElement.classList.toggle('dark',!dark);setDark(!dark)}}>{dark?'Light theme':'Dark theme'}</button></div>
 <SourceMixBar mix={{kb:1,web:1,general:1,unit:'citations'}}/><div className="mt-5">
 {active===cases[0]&&<AnalysisCard analysis={base as any}/>}
 {active===cases[1]&&<AnalysisCard analysis={pie as any}/>}
 {active===cases[2]&&<AnswerVisualization spec={{kind:'chart2d',title:'Synthetic research plot — no axes supplied',data:[{type:'scatter',mode:'lines+markers',x:[1,2,3],y:[10,20,15]}],layout:{}}}/>}
 {active===cases[3]&&<AnswerEquations equations={[equation as any]}/>}
 {active===cases[4]&&<PaperMap projectId="audit-two"/>}
 {active===cases[5]&&<PaperMap projectId="audit-one"/>}
 {active===cases[6]&&<AnswerVisualization spec={{kind:'diagram',title:'Synthetic evidence workflow',mermaid:'flowchart LR\nA[Document] --> B[Extracted evidence]\nB --> C[Verified claim]'}}/>}
 </div></main>
}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}><MotionConfig reducedMotion="user"><Audit/></MotionConfig></QueryClientProvider>);
