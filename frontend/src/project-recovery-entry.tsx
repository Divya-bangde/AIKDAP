/** Disposable read-only project fixture for testing the real lazy tab boundary. */
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MotionConfig } from "motion/react";
import { ProjectDetail } from "@/pages/ProjectDetail";
import { useAuthStore } from "@/store/auth-store";
import "@/index.css";

const client = new QueryClient({defaultOptions:{queries:{staleTime:Infinity,retry:false}}});
const id="fixture-project";
const session=useAuthStore.getState().sessionId;
client.setQueryData(["projects",id],{id,name:"Synthetic project recovery fixture",project_type:"research",description:"No live project data or import actions.",status:"active",created_at:"2026-10-17T00:00:00Z",updated_at:"2026-10-17T00:00:00Z"});
client.setQueryData(["assets",id],[]);
client.setQueryData(["research","runs",id],[]);
client.setQueryData(["research","runs","pages",id,"all",session],{pages:[{items:[],next_cursor:null}],pageParams:[null]});
client.setQueryData(["projects","summary",id],{documents:0,projects:1,assets:0,runs:0,grounded_answers:0});
client.setQueryData(["knowledge-readiness",id,session],{ready:true,compatible:true,total_chunks:0,searchable_chunks:0,legacy_chunks:0,configured_space:{provider:"fixture",model:"synthetic",dimensions:1},rebuild:null});
client.setQueryData(["paper-graph",id,false],{nodes:[{id:"W1",title:"Synthetic paper A",year:2026,cited_by_count:1,kind:"uploaded",in_project:true,doi:null,asset_id:null},{id:"W2",title:"Synthetic paper B",year:2026,cited_by_count:0,kind:"uploaded",in_project:true,doi:null,asset_id:null}],links:[{source:"W1",target:"W2"}],unmatched:[],external_unavailable:false});
createRoot(document.getElementById("root")!).render(<MemoryRouter initialEntries={[`/projects/${id}?tab=papers`]}><QueryClientProvider client={client}><MotionConfig reducedMotion="always"><main className="mx-auto max-w-6xl p-4"><p className="mb-4 text-xs">Synthetic local fixture · production project tab · no project writes</p><Routes><Route path="/projects/:id" element={<ProjectDetail/>}/></Routes></main></MotionConfig></QueryClientProvider></MemoryRouter>);
